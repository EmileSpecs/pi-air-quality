"""Latest air reading and the JSON document Home Assistant stores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Reading:
    formaldehyde_ppb: float | None = None
    temperature_c: float | None = None
    humidity_pct: float | None = None
    formaldehyde_status: str = "missing"
    pm1: float | None = None
    pm25: float | None = None
    pm4: float | None = None
    pm10: float | None = None
    voc_index: float | None = None
    nox_index: float | None = None
    co2_ppm: int | None = None
    co2_calibration_status: str = ""
    detail: str = ""
    version: str = ""
    updated: str = ""

    def public_state(self, *, formaldehyde: bool, sen69c: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"version": self.version, "updated": self.updated}
        publish_hcho = formaldehyde or sen69c
        publish_climate = formaldehyde or sen69c
        if publish_hcho:
            payload["formaldehyde_status"] = self.formaldehyde_status
            if self.formaldehyde_status in {"stabilizing", "ready"} and self.formaldehyde_ppb is not None:
                payload["formaldehyde_ppb"] = self.formaldehyde_ppb
        if publish_climate:
            if self.temperature_c is not None:
                payload["temperature_c"] = self.temperature_c
            if self.humidity_pct is not None:
                payload["humidity_pct"] = self.humidity_pct
        if sen69c:
            if self.pm1 is not None:
                payload["pm1"] = self.pm1
            if self.pm25 is not None:
                payload["pm25"] = self.pm25
            if self.pm4 is not None:
                payload["pm4"] = self.pm4
            if self.pm10 is not None:
                payload["pm10"] = self.pm10
            if self.voc_index is not None:
                payload["voc_index"] = self.voc_index
            if self.nox_index is not None:
                payload["nox_index"] = self.nox_index
            if self.co2_ppm is not None:
                payload["co2_ppm"] = self.co2_ppm
        return payload
