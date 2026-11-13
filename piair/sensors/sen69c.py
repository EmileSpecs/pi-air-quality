"""Sensirion SEN69C: PM, climate, VOC, NOx, formaldehyde, and CO2."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from piair.sensors.bus import I2CBus
from piair.sensors.crc import check_word, crc8

log = logging.getLogger("piair.sensors.sen69c")

ADDRESS = 0x6B
_START = bytes((0x00, 0x21))
_STOP = bytes((0x01, 0x04))
_READ = bytes((0x04, 0xB5))
_ASC = bytes((0x67, 0x11))
_PRESSURE = bytes((0x67, 0x20))
_FRC = bytes((0x67, 0x07))
_START_DELAY_S = 0.05
_READ_DELAY_S = 0.02
_STOP_SETTLE_S = 1.5
_ASC_DELAY_S = 0.05
_PRESSURE_DELAY_S = 0.05
_START_SETTLE_S = 0.1
_FRC_DELAY_S = 0.5
_UNKNOWN_U16 = 0xFFFF
_UNKNOWN_I16 = 0x7FFF
_FRC_FAIL = 0xFFFF
_FRC_OFFSET = 0x8000


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


def _word_command(command: bytes, value: int) -> bytes:
    payload = value.to_bytes(2, "big")
    return command + payload + bytes((crc8(payload),))


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

    def stop(self) -> None:
        self.bus.write(self.address, _STOP)
        self._started = False

    def read(self) -> Sen69cSample:
        if not self._started:
            self.start()
        frame = self.bus.write_delay_read(self.address, _READ, 30, _READ_DELAY_S)
        return parse_measurement(frame)

    def set_automatic_self_calibration(self, enabled: bool) -> None:
        """Idle mode only. Setting is volatile across reset."""
        value = 1 if enabled else 0
        self.bus.write(self.address, _word_command(_ASC, value))
        if enabled:
            log.info("ASC enabled")
        else:
            log.info("ASC disabled")

    def set_ambient_pressure(self, hpa: int) -> None:
        """Allowed during measurement. Volatile across reset. Valid 700–1200 hPa."""
        if not 700 <= hpa <= 1200:
            raise ValueError("ambient pressure must be between 700 and 1200 hPa")
        self.bus.write(self.address, _word_command(_PRESSURE, hpa))
        log.info("Ambient pressure set to %s hPa", hpa)

    def perform_forced_co2_recalibration(self, target_ppm: int) -> int:
        """Send FRC in idle mode. Returns correction ppm, or raises on 0xFFFF."""
        if not 350 <= target_ppm <= 1200:
            raise ValueError("CO2 calibration target must be between 350 and 1200 ppm")
        frame = self.bus.write_delay_read(
            self.address,
            _word_command(_FRC, target_ppm),
            3,
            _FRC_DELAY_S,
        )
        if len(frame) != 3:
            raise ValueError("FRC reply must be 3 bytes")
        check_word(frame[0:2], frame[2])
        raw = int.from_bytes(frame[0:2], "big")
        if raw == _FRC_FAIL:
            raise RuntimeError("Forced recalibration FAILED (0xFFFF)")
        return int(raw) - _FRC_OFFSET

    def configure_co2(self, pressure_hpa: int, *, asc_enabled: bool = False) -> None:
        """stop → ASC → pressure → start → pressure (AirQ Pro timings)."""
        log.info("CO2 setup: configuring ASC and ambient pressure")
        try:
            self.stop()
        except OSError as exc:
            log.warning("Stop measurement failed: %s", exc)
        time.sleep(_STOP_SETTLE_S)
        self.set_automatic_self_calibration(asc_enabled)
        time.sleep(_ASC_DELAY_S)
        self.set_ambient_pressure(pressure_hpa)
        time.sleep(_PRESSURE_DELAY_S)
        self.start()
        time.sleep(_START_SETTLE_S)
        self.set_ambient_pressure(pressure_hpa)

    def calibrate_co2(self, target_ppm: int, pressure_hpa: int) -> int:
        """stop → ASC off → pressure → FRC → start → pressure."""
        log.info(
            "CO2 FRC starting (target %s ppm) — leave device in fresh outdoor air",
            target_ppm,
        )
        try:
            self.stop()
        except OSError as exc:
            log.warning("Stop measurement failed: %s", exc)
        time.sleep(_STOP_SETTLE_S)
        self.set_automatic_self_calibration(False)
        time.sleep(_ASC_DELAY_S)
        self.set_ambient_pressure(pressure_hpa)
        time.sleep(_PRESSURE_DELAY_S)
        try:
            correction = self.perform_forced_co2_recalibration(target_ppm)
        except RuntimeError:
            log.error("Forced recalibration FAILED (0xFFFF) — reading unchanged")
            try:
                self.start()
                time.sleep(_START_SETTLE_S)
                self.set_ambient_pressure(pressure_hpa)
            except OSError:
                pass
            raise
        log.info("Forced recalibration OK, correction %+.0f ppm", correction)
        self.start()
        time.sleep(_START_SETTLE_S)
        self.set_ambient_pressure(pressure_hpa)
        return correction

    def close(self) -> None:
        if self._started:
            try:
                self.stop()
            except OSError:
                pass
