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
    pm1: int | None = None
    pm25: int | None = None
    pm10: int | None = None
    co2_ppm: int | None = None
    detail: str = ""
    version: str = ""
    updated: str = ""

    def public_state(self, *, formaldehyde: bool, particulate: bool, co2: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"version": self.version, "updated": self.updated}
        if formaldehyde:
            payload["formaldehyde_status"] = self.formaldehyde_status
            publish_hcho = self.formaldehyde_status in {"stabilizing", "ready"}
            if publish_hcho and self.formaldehyde_ppb is not None:
                payload["formaldehyde_ppb"] = self.formaldehyde_ppb
            if self.temperature_c is not None:
                payload["temperature_c"] = self.temperature_c
            if self.humidity_pct is not None:
                payload["humidity_pct"] = self.humidity_pct
        if particulate:
            if self.pm1 is not None:
                payload["pm1"] = self.pm1
            if self.pm25 is not None:
                payload["pm25"] = self.pm25
            if self.pm10 is not None:
                payload["pm10"] = self.pm10
        if co2 and self.co2_ppm is not None:
            payload["co2_ppm"] = self.co2_ppm
        return payload
