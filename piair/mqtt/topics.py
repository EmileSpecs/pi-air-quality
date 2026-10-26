"""Single place for topic names."""

from __future__ import annotations

from piair.config import AirConfig


class Topics:
    def __init__(self, config: AirConfig) -> None:
        self.base = f"{config.mqtt.base_topic}/{config.device.id}"
        self.discovery = config.mqtt.discovery_prefix
        self.device_id = config.device.id
        self.node = f"piair_{config.device.id}"

    def status(self) -> str:
        return f"{self.base}/status"

    def state(self) -> str:
        return f"{self.base}/state"

    def command(self, name: str) -> str:
        return f"{self.base}/cmd/{name}"

    def birth(self) -> str:
        return f"{self.discovery}/status"

    def discovery_topic(self, component: str, object_id: str) -> str:
        return f"{self.discovery}/{component}/{self.node}/{object_id}/config"
