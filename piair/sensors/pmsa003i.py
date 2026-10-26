"""Plantower PMSA003I particulate sensor."""

from __future__ import annotations

from dataclasses import dataclass

from piair.sensors.bus import I2CBus

ADDRESS = 0x12
_FRAME = 32


@dataclass(frozen=True)
class PmSample:
    pm1: int
    pm25: int
    pm10: int


def parse_frame(frame: bytes) -> PmSample:
    if len(frame) != _FRAME:
        raise ValueError("PMSA003I frame must be 32 bytes")
    if frame[0] != 0x42 or frame[1] != 0x4D:
        raise ValueError("PMSA003I frame header is invalid")
    expected = int.from_bytes(frame[30:32], "big")
    if sum(frame[:30]) & 0xFFFF != expected:
        raise ValueError("PMSA003I checksum mismatch")
    # Atmospheric concentrations, not the CF=1 workshop values.
    return PmSample(
        pm1=int.from_bytes(frame[10:12], "big"),
        pm25=int.from_bytes(frame[12:14], "big"),
        pm10=int.from_bytes(frame[14:16], "big"),
    )


class Pmsa003i:
    def __init__(self, bus: I2CBus, address: int = ADDRESS) -> None:
        self.bus = bus
        self.address = address

    def start(self) -> None:
        return None

    def read(self) -> PmSample:
        frame = self.bus.write_read(self.address, bytes([0x00]), _FRAME)
        return parse_frame(frame)

    def close(self) -> None:
        return None
