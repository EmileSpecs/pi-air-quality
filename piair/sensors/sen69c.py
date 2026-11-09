"""Sensirion SEN69C: PM, climate, VOC, NOx, formaldehyde, and CO2."""

from __future__ import annotations

import time
from dataclasses import dataclass

from piair.sensors.bus import I2CBus
from piair.sensors.crc import check_word

ADDRESS = 0x6B
_START = bytes((0x00, 0x21))
_STOP = bytes((0x01, 0x04))
_READ = bytes((0x04, 0xB5))
_START_DELAY_S = 0.05
_READ_DELAY_S = 0.02
_UNKNOWN_U16 = 0xFFFF
_UNKNOWN_I16 = 0x7FFF


@dataclass(frozen=True)
class Sen69cSample:
    pm1: float | None
    pm25: float | None
    pm4: float | None
    pm10: float | None
    humidity_pct: float | None
    temperature_c: float | None
    voc_index: float | None
    nox_index: float | None
    formaldehyde_ppb: float | None
    co2_ppm: int | None
    formaldehyde_status: str


def _u16(frame: bytes, offset: int) -> int:
    return int.from_bytes(frame[offset : offset + 2], "big")


def _i16(frame: bytes, offset: int) -> int:
    return int.from_bytes(frame[offset : offset + 2], "big", signed=True)


def _scale_u16(raw: int, divisor: float) -> float | None:
    if raw == _UNKNOWN_U16:
        return None
    return round(raw / divisor, 1)


def _scale_i16(raw: int, divisor: float, places: int = 1) -> float | None:
    if raw == _UNKNOWN_I16:
        return None
    return round(raw / divisor, places)


def parse_measurement(frame: bytes) -> Sen69cSample:
    if len(frame) != 30:
        raise ValueError("SEN69C frame must be 30 bytes")
    for offset in range(0, 30, 3):
        check_word(frame[offset : offset + 2], frame[offset + 2])

    formaldehyde = _scale_u16(_u16(frame, 24), 10)
    status = "warming_up" if formaldehyde is None else "ready"
    co2_raw = _i16(frame, 27)
    co2 = None if co2_raw == _UNKNOWN_I16 else int(co2_raw)
    return Sen69cSample(
        pm1=_scale_u16(_u16(frame, 0), 10),
        pm25=_scale_u16(_u16(frame, 3), 10),
        pm4=_scale_u16(_u16(frame, 6), 10),
        pm10=_scale_u16(_u16(frame, 9), 10),
        humidity_pct=_scale_i16(_i16(frame, 12), 100, places=2),
        temperature_c=_scale_i16(_i16(frame, 15), 200, places=2),
        voc_index=_scale_i16(_i16(frame, 18), 10),
        nox_index=_scale_i16(_i16(frame, 21), 10),
        formaldehyde_ppb=formaldehyde,
        co2_ppm=co2,
        formaldehyde_status=status,
    )


class Sen69c:
    def __init__(self, bus: I2CBus, address: int = ADDRESS) -> None:
        self.bus = bus
        self.address = address
        self._started = False

    def start(self) -> None:
        self.bus.write(self.address, _START)
        time.sleep(_START_DELAY_S)
        self._started = True

    def read(self) -> Sen69cSample:
        if not self._started:
            self.start()
        frame = self.bus.write_delay_read(self.address, _READ, 30, _READ_DELAY_S)
        return parse_measurement(frame)

    def close(self) -> None:
        if self._started:
            try:
                self.bus.write(self.address, _STOP)
            except OSError:
                pass
            self._started = False
