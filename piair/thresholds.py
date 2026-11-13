"""AirQ Pro value bands for status coloring."""

from __future__ import annotations

from typing import Literal

Band = Literal["green", "yellow", "amber", "red"]

# value >= threshold enters that band; green below yellow.
THRESHOLDS: dict[str, tuple[float, float, float]] = {
    "pm1": (15, 25, 55),
    "pm25": (15, 25, 55),
    "pm4": (15, 25, 55),
    "pm10": (45, 80, 150),
    "co2": (800, 1000, 1500),
    "formaldehyde": (40, 61, 100),
    "voc_index": (150, 250, 400),
    "nox_index": (20, 50, 150),
}


def band_for(channel: str, value: float | None) -> Band | None:
    if value is None:
        return None
    yellow, amber, red = THRESHOLDS[channel]
    if value >= red:
        return "red"
    if value >= amber:
        return "amber"
    if value >= yellow:
        return "yellow"
    return "green"
