"""Sensirion SFA40 formaldehyde, temperature, and humidity."""

from __future__ import annotations

from dataclasses import dataclass

from piair.sensors.bus import I2CBus
from piair.sensors.crc import check_word, crc8

ADDRESS = 0x5D
# Command bytes. The second byte is the CRC-8 of the first. A general-call
# reset is intentionally not implemented: it would reset every device on the bus.
_START = bytes((0x00, crc8(bytes([0x00]))))
_READ = bytes((0xC0, crc8(bytes([0xC0]))))
_STOP = bytes((0x50, crc8(bytes([0x50]))))


@dataclass(frozen=True)
class Sfa40Sample:
    formaldehyde_ppb: float | None
    temperature_c: float | None
    humidity_pct: float | None
    status: str


def _scale_humidity(ticks: int) -> float:
    value = -6 + 125 * ticks / 65535
    return round(min(100.0, max(0.0, value)), 2)


def _scale_temperature(ticks: int) -> float:
    return round(-45 + 175 * ticks / 65535, 2)


def status_name(status: int) -> str:
    """Bit 0 is the first minute. Bit 1 is minutes 1-10, not yet in specification."""
    if status & 0x01:
        return "warming_up"
    if status & 0x02:
        return "stabilizing"
    return "ready"


def parse_measurement(frame: bytes) -> Sfa40Sample:
    if len(frame) != 12:
        raise ValueError("SFA40 frame must be 12 bytes")
    check_word(frame[0:2], frame[2])
    check_word(frame[3:5], frame[5])
    check_word(frame[6:8], frame[8])
    check_word(frame[9:11], frame[11])
    status = status_name(frame[9])
    formaldehyde = None if status == "warming_up" else round(int.from_bytes(frame[0:2], "big") / 10, 1)
    return Sfa40Sample(
        formaldehyde_ppb=formaldehyde,
        temperature_c=_scale_temperature(int.from_bytes(frame[6:8], "big")),
        humidity_pct=_scale_humidity(int.from_bytes(frame[3:5], "big")),
        status=status,
    )


class Sfa40:
    def __init__(self, bus: I2CBus, address: int = ADDRESS) -> None:
        self.bus = bus
        self.address = address
        self._started = False

    def start(self) -> None:
        self.bus.write(self.address, _START)
        self._started = True

    def read(self) -> Sfa40Sample:
        if not self._started:
            self.start()
        frame = self.bus.write_read(self.address, _READ, 12)
        return parse_measurement(frame)

    def close(self) -> None:
        if self._started:
            try:
                self.bus.write(self.address, _STOP)
            except OSError:
                pass
            self._started = False
