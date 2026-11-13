"""Home Assistant MQTT discovery payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from piair import __version__
from piair.config import AirConfig
from piair.mqtt.topics import Topics


@dataclass(frozen=True)
class DiscoveryContext:
    ip: str = ""
    hw_version: str = "Raspberry Pi 5"
    sw_version: str = __version__
    support_url: str = "https://github.com/EmileSpecs/pi-air-quality"


def _device(config: AirConfig, ctx: DiscoveryContext) -> dict[str, Any]:
    device: dict[str, Any] = {
        "identifiers": [f"piair_{config.device.id}"],
        "name": config.device.name,
        "manufacturer": config.device.manufacturer,
        "model": config.device.model,
        "sw_version": ctx.sw_version,
        "hw_version": ctx.hw_version,
    }
    if config.device.suggested_area:
        device["suggested_area"] = config.device.suggested_area
    if config.admin.enabled and ctx.ip:
        device["configuration_url"] = f"http://{ctx.ip}:{config.admin.port}"
    return device


def _shared(config: AirConfig, ctx: DiscoveryContext) -> dict[str, Any]:
    topics = Topics(config)
    return {
        "availability": [{"topic": topics.status()}],
        "availability_mode": "all",
        "device": _device(config, ctx),
        "origin": {
            "name": "pi-air-quality",
            "sw_version": ctx.sw_version,
            "support_url": ctx.support_url or config.support_url(),
        },
        "has_entity_name": True,
        "qos": 1,
    }


def _identity(config: AirConfig, component: str, object_id: str, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "unique_id": f"piair_{config.device.id}_{object_id}",
        "default_entity_id": f"{component}.{config.device.id}_{object_id}",
    }


def _sensor(
    config: AirConfig,
    topics: Topics,
    object_id: str,
    name: str,
    field: str,
    **extra: Any,
) -> dict[str, Any]:
    body = {
        **_identity(config, "sensor", object_id, name),
        "state_topic": topics.state(),
        "value_template": f"{{{{ value_json.{field} }}}}",
    }
    body.update(extra)
    return body


def _add_formaldehyde(add, config: AirConfig, topics: Topics) -> None:
    add(
        "sensor",
        "formaldehyde",
        _sensor(
            config,
            topics,
            "formaldehyde",
            "Formaldehyde",
            "formaldehyde_ppb",
            unit_of_measurement="ppb",
            state_class="measurement",
            icon="mdi:molecule",
        ),
    )
    add(
        "sensor",
        "formaldehyde_status",
        _sensor(
            config,
            topics,
            "formaldehyde_status",
            "Formaldehyde status",
            "formaldehyde_status",
            entity_category="diagnostic",
            icon="mdi:timer-sand",
        ),
    )


def _add_climate(add, config: AirConfig, topics: Topics) -> None:
    add(
        "sensor",
        "temperature",
        _sensor(
            config,
            topics,
            "temperature",
            "Temperature",
            "temperature_c",
            device_class="temperature",
            unit_of_measurement="°C",
            state_class="measurement",
        ),
    )
    add(
        "sensor",
        "humidity",
        _sensor(
            config,
            topics,
            "humidity",
            "Humidity",
            "humidity_pct",
            device_class="humidity",
            unit_of_measurement="%",
            state_class="measurement",
        ),
    )


def _add_co2_controls(add, config: AirConfig, topics: Topics) -> None:
    add(
        "number",
        "ambient_pressure",
        {
            **_identity(config, "number", "ambient_pressure", "Ambient pressure"),
            "command_topic": topics.command("ambient_pressure"),
            "state_topic": topics.state(),
            "value_template": "{{ value_json.ambient_pressure_hpa }}",
            "min": 700,
            "max": 1200,
            "step": 1,
            "unit_of_measurement": "hPa",
            "device_class": "atmospheric_pressure",
            "entity_category": "config",
            "mode": "box",
            "icon": "mdi:gauge",
        },
    )
    add(
        "number",
        "co2_calibration_target",
        {
            **_identity(config, "number", "co2_calibration_target", "CO2 calibration target"),
            "command_topic": topics.command("co2_calibration_target"),
            "state_topic": topics.state(),
            "value_template": "{{ value_json.co2_calibration_target_ppm }}",
            "min": 350,
            "max": 1200,
            "step": 1,
            "unit_of_measurement": "ppm",
            "entity_category": "config",
            "mode": "box",
            "icon": "mdi:target",
        },
    )
    add(
        "button",
        "calibrate_co2",
        {
            **_identity(config, "button", "calibrate_co2", "Calibrate CO2 now"),
            "command_topic": topics.command("calibrate_co2"),
            "payload_press": "PRESS",
            "entity_category": "config",
            "icon": "mdi:molecule-co2",
        },
    )
    add(
        "button",
        "apply_co2_setup",
        {
            **_identity(config, "button", "apply_co2_setup", "Apply CO2 setup"),
            "command_topic": topics.command("apply_co2_setup"),
            "payload_press": "PRESS",
            "entity_category": "config",
            "icon": "mdi:cog-sync",
        },
    )
    add(
        "sensor",
        "co2_calibration_status",
        _sensor(
            config,
            topics,
            "co2_calibration_status",
            "CO2 calibration status",
            "co2_calibration_status",
            entity_category="diagnostic",
            icon="mdi:information-outline",
        ),
    )


def build_component_payloads(
    config: AirConfig,
    ctx: DiscoveryContext | None = None,
) -> dict[str, dict[str, Any]]:
    context = ctx or DiscoveryContext(support_url=config.support_url())
    topics = Topics(config)
    built: dict[str, dict[str, Any]] = {}

    def add(component: str, object_id: str, body: dict[str, Any]) -> None:
        payload = _shared(config, context)
        payload.update(body)
        built[topics.discovery_topic(component, object_id)] = payload

    sensors = config.sensors
    publish_hcho = sensors.formaldehyde.enabled or sensors.sen69c.enabled
    publish_climate = sensors.formaldehyde.enabled or sensors.sen69c.enabled

    if publish_hcho:
        _add_formaldehyde(add, config, topics)
    if publish_climate:
        _add_climate(add, config, topics)

    if sensors.sen69c.enabled:
        for object_id, name, field, device_class in (
            ("pm1", "PM1", "pm1", "pm1"),
            ("pm25", "PM2.5", "pm25", "pm25"),
            ("pm4", "PM4", "pm4", None),
            ("pm10", "PM10", "pm10", "pm10"),
        ):
            extra: dict[str, Any] = {
                "unit_of_measurement": "µg/m³",
                "state_class": "measurement",
            }
            if device_class is not None:
                extra["device_class"] = device_class
            else:
                extra["icon"] = "mdi:dots-hexagon"
            add("sensor", object_id, _sensor(config, topics, object_id, name, field, **extra))
        add(
            "sensor",
            "voc_index",
            _sensor(
                config,
                topics,
                "voc_index",
                "VOC index",
                "voc_index",
                state_class="measurement",
                icon="mdi:smell",
            ),
        )
        add(
            "sensor",
            "nox_index",
            _sensor(
                config,
                topics,
                "nox_index",
                "NOx index",
                "nox_index",
                state_class="measurement",
                icon="mdi:molecule-co",
            ),
        )
        add(
            "sensor",
            "co2",
            _sensor(
                config,
                topics,
                "co2",
                "CO2",
                "co2_ppm",
                device_class="carbon_dioxide",
                unit_of_measurement="ppm",
                state_class="measurement",
            ),
        )
        _add_co2_controls(add, config, topics)

    add(
        "sensor",
        "version",
        _sensor(
            config,
            topics,
            "version",
            "Version",
            "version",
            entity_category="diagnostic",
            icon="mdi:git",
        ),
    )
    add(
        "button",
        "update",
        {
            **_identity(config, "button", "update", "Update"),
            "command_topic": topics.command("update"),
            "payload_press": "PRESS",
            "entity_category": "config",
            "icon": "mdi:update",
        },
    )
    return built
