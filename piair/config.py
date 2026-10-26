"""Configuration models, YAML loading, and !secret resolution."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEVICE_ID_RE = re.compile(r"^[a-z0-9_]{3,32}$")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(Exception):
    """A config file is missing or invalid. Process should exit 2."""


class SecretRef:
    def __init__(self, key: str) -> None:
        self.key = key

    def __repr__(self) -> str:
        return f"!secret {self.key}"


class _Loader(yaml.SafeLoader):
    pass


def _secret_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> SecretRef:
    value = loader.construct_scalar(node)  # type: ignore[arg-type]
    return SecretRef(str(value))


_Loader.add_constructor("!secret", _secret_constructor)


class TlsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    ca_file: str | None = None
    insecure: bool = False


class ReconnectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_delay_s: float = 1
    max_delay_s: float = 60


class DeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    manufacturer: str = "DIY"
    model: str = "Raspberry Pi 5 Air Quality"
    suggested_area: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not DEVICE_ID_RE.match(value):
            raise ValueError("device.id must match ^[a-z0-9_]{3,32}$")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not 1 <= len(value) <= 64:
            raise ValueError("device.name must be 1-64 characters")
        return value


class MqttConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    keepalive: int = 30
    base_topic: str = "piair"
    discovery_prefix: str = "homeassistant"
    tls: TlsConfig = Field(default_factory=TlsConfig)
    reconnect: ReconnectConfig = Field(default_factory=ReconnectConfig)

    @field_validator("base_topic", "discovery_prefix")
    @classmethod
    def _topic(cls, value: str) -> str:
        if not TOPIC_RE.match(value):
            raise ValueError("topic names must be letters, numbers, _ or -")
        return value

    def resolved_client_id(self, device_id: str) -> str:
        return self.client_id or f"piair-{device_id}"

    def connection_key(self, device_id: str) -> tuple[object, ...]:
        tls = self.tls
        return (
            self.host,
            self.port,
            self.username,
            self.password,
            self.client_id,
            self.keepalive,
            self.base_topic,
            self.discovery_prefix,
            tls.enabled,
            tls.ca_file,
            tls.insecure,
            device_id,
        )


class SensorBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False


class SensorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bus: int = 1
    interval_s: float = 1
    publish_interval_s: float = 10
    formaldehyde: SensorBlock = Field(default_factory=lambda: SensorBlock(enabled=True))
    particulate: SensorBlock = Field(default_factory=SensorBlock)
    co2: SensorBlock = Field(default_factory=SensorBlock)

    @field_validator("interval_s", "publish_interval_s")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("intervals must be greater than 0")
        return value


class AdminConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    bind: str = "0.0.0.0"
    port: int = 8092
    password: str | None = None


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError("logging.level must be DEBUG, INFO, WARNING, or ERROR")
        return upper


class UpdateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto: bool = True
    repo: str | None = None
    ref: str = "main"


class AirConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: DeviceConfig
    mqtt: MqttConfig
    sensors: SensorsConfig = Field(default_factory=SensorsConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)

    def support_url(self) -> str:
        if self.update.repo:
            cleaned = self.update.repo.removesuffix(".git")
            if cleaned.startswith("git@"):
                cleaned = "https://" + cleaned[4:].replace(":", "/", 1)
            return cleaned
        return "https://github.com/EmileSpecs/pi-air-quality"


def locked_device_id(locked: bool, current: str, requested: str) -> str:
    if not DEVICE_ID_RE.match(requested):
        raise ConfigError("device.id must match ^[a-z0-9_]{3,32}$")
    if locked and requested != current:
        raise ConfigError("device id is locked after the first connection")
    return requested


def _resolve_secrets(value: Any, secrets: dict[str, Any]) -> Any:
    if isinstance(value, SecretRef):
        if value.key not in secrets:
            raise ConfigError(f"secret {value.key!r} is not defined in secrets.yaml")
        return secrets[value.key]
    if isinstance(value, dict):
        return {key: _resolve_secrets(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_secrets(item, secrets) for item in value]
    return value


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def apply_env(data: dict[str, Any], environ: dict[str, str] | None = None) -> None:
    source = environ if environ is not None else dict(os.environ)
    for key, raw in source.items():
        if not key.startswith("PIAIR__"):
            continue
        parts = [part.lower() for part in key.split("__")[1:] if part]
        if not parts:
            continue
        cursor: dict[str, Any] = data
        for part in parts[:-1]:
            current = cursor.get(part)
            if not isinstance(current, dict):
                current = {}
                cursor[part] = current
            cursor = current
        cursor[parts[-1]] = _coerce(raw)


def secrets_path_for(config_path: Path) -> Path:
    sibling = config_path.parent / "secrets.yaml"
    if sibling.exists():
        return sibling
    example = config_path.parent / "secrets.example.yaml"
    if example.exists():
        return example
    return sibling


def load_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        loaded = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    return loaded or {}


def load_config(
    path: str | Path,
    *,
    secrets_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> AirConfig:
    config_path = Path(path)
    raw = load_yaml(config_path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a mapping")
    secret_file = Path(secrets_path) if secrets_path else secrets_path_for(config_path)
    secrets: dict[str, Any] = {}
    if secret_file.exists():
        loaded = load_yaml(secret_file)
        if not isinstance(loaded, dict):
            raise ConfigError(f"{secret_file} must contain a mapping")
        secrets = loaded
    resolved = _resolve_secrets(raw, secrets)
    if not isinstance(resolved, dict):
        raise ConfigError(f"{config_path} must contain a mapping")
    apply_env(resolved, environ)
    try:
        return AirConfig.model_validate(resolved)
    except ValidationError as exc:
        lines = [f"{config_path}: invalid configuration"]
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            lines.append(f"  {loc}: {error['msg']}")
        raise ConfigError("\n".join(lines)) from exc


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.05)


class _Dumper(yaml.SafeDumper):
    pass


def _represent_secret(dumper: yaml.SafeDumper, data: SecretRef) -> yaml.Node:
    return dumper.represent_scalar("!secret", data.key)


_Dumper.add_representer(SecretRef, _represent_secret)


class ConfigStore:
    def __init__(self, config_path: Path, secrets_path: Path) -> None:
        self.config_path = config_path
        self.secrets_path = secrets_path
        self.config = load_config(config_path, secrets_path=secrets_path)
        self.secrets = self._read_secrets()

    def _read_secrets(self) -> dict[str, Any]:
        if not self.secrets_path.exists():
            return {}
        loaded = yaml.load(self.secrets_path.read_text(encoding="utf-8"), Loader=_Loader)
        return loaded if isinstance(loaded, dict) else {}

    def reload(self) -> AirConfig:
        self.secrets = self._read_secrets()
        self.config = load_config(self.config_path, secrets_path=self.secrets_path)
        return self.config

    def save(self, config: AirConfig, secrets: dict[str, Any]) -> None:
        data = config.model_dump(mode="json")
        data["mqtt"]["password"] = SecretRef("mqtt_password")
        data["admin"]["password"] = SecretRef("admin_password")
        text = yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True)
        atomic_write_text(self.config_path, text)
        atomic_write_text(self.secrets_path, yaml.safe_dump(secrets, sort_keys=False))
        self.config = load_config(self.config_path, secrets_path=self.secrets_path)
        self.secrets = secrets
