"""Small JSON file that remembers the device-id lock and discovery topics."""

from __future__ import annotations

import json
from pathlib import Path

from piair.config import atomic_write_text


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.device_id_locked = False
        self.discovery_topics: list[str] = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        self.device_id_locked = bool(raw.get("device_id_locked"))
        topics = raw.get("discovery_topics")
        if isinstance(topics, list):
            self.discovery_topics = [str(item) for item in topics]

    def save(self) -> None:
        payload = {
            "device_id_locked": self.device_id_locked,
            "discovery_topics": self.discovery_topics,
        }
        atomic_write_text(self.path, json.dumps(payload, indent=2) + "\n")
