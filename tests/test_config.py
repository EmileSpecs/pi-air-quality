"""Config secrets, environment overrides, and the device-id lock."""

from pathlib import Path

import pytest

from piair.config import ConfigError, ConfigStore, load_config, locked_device_id
from piair.__main__ import main

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"


def _write(directory: Path) -> Path:
    config = directory / "config.yaml"
    secrets = directory / "secrets.yaml"
    config.write_text(
        EXAMPLE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    secrets.write_text(
        'mqtt_password: "secret-mqtt"\nadmin_password: "secret-admin"\n',
        encoding="utf-8",
    )
    return config


def test_secrets_and_env_override(tmp_path: Path) -> None:
    config = load_config(
        _write(tmp_path),
        environ={"PIAIR__MQTT__HOST": "10.1.1.8", "PIAIR__SENSORS__CO2__ENABLED": "true"},
    )
    assert config.mqtt.password == "secret-mqtt"
    assert config.admin.password == "secret-admin"
    assert config.mqtt.host == "10.1.1.8"
    assert config.sensors.co2.enabled is True
    assert config.sensors.formaldehyde.enabled is True
    assert config.sensors.particulate.enabled is False


def test_missing_secret(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ConfigError, match="mqtt_password"):
        load_config(config, secrets_path=tmp_path / "missing.yaml")


def test_device_id_lock() -> None:
    assert locked_device_id(False, "lounge_air", "hall_air") == "hall_air"
    with pytest.raises(ConfigError, match="locked"):
        locked_device_id(True, "lounge_air", "hall_air")
    with pytest.raises(ConfigError, match="device.id"):
        locked_device_id(False, "lounge_air", "No")


def test_save_keeps_secret_tags(tmp_path: Path) -> None:
    path = _write(tmp_path)
    store = ConfigStore(path, tmp_path / "secrets.yaml")
    updated = store.config.model_copy(
        update={"device": store.config.device.model_copy(update={"name": "Hall air"})}
    )
    store.save(updated, store.secrets)
    text = path.read_text(encoding="utf-8")
    assert "mqtt_password" in text
    assert "admin_password" in text
    assert "secret-mqtt" not in text
    reloaded = load_config(path)
    assert reloaded.device.name == "Hall air"
    assert reloaded.mqtt.password == "secret-mqtt"


def test_check_config(tmp_path: Path) -> None:
    assert main(["--check-config", str(_write(tmp_path))]) == 0
