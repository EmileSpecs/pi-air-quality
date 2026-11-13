"""Retained state document."""

from __future__ import annotations

import json
from typing import Any

from piair.config import AirConfig
from piair.reading import Reading


def state_payload(config: AirConfig, reading: Reading) -> dict[str, Any]:
    sensors = config.sensors
    payload = reading.public_state(
        formaldehyde=sensors.formaldehyde.enabled,
        sen69c=sensors.sen69c.enabled,
    )
    if sensors.sen69c.enabled:
        payload["ambient_pressure_hpa"] = sensors.sen69c.ambient_pressure_hpa
        payload["co2_calibration_target_ppm"] = sensors.sen69c.co2_calibration_target_ppm
        if reading.co2_calibration_status:
            payload["co2_calibration_status"] = reading.co2_calibration_status
    return payload


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
