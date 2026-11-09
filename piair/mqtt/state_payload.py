"""Retained state document."""

from __future__ import annotations

import json
from typing import Any

from piair.config import AirConfig
from piair.reading import Reading


def state_payload(config: AirConfig, reading: Reading) -> dict[str, Any]:
    sensors = config.sensors
    return reading.public_state(
        formaldehyde=sensors.formaldehyde.enabled,
        sen69c=sensors.sen69c.enabled,
    )


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
