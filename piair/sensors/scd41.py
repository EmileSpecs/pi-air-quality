"""Sensirion SCD41 carbon dioxide sensor.

Temperature and humidity are decoded so a bad frame is rejected, then dropped.
Room climate stays on the formaldehyde board.
"""

from __future__ import annotations

from dataclasses import dataclass

from piair.sensors.bus import I2CBus
from piair.sensors.crc import check_word

ADDRESS = 0x62
_START = bytes((0x21, 0xB1))
_DATA_READY = bytes((0xE4, 0xB8))
_READ = bytes((0xEC, 0x05))
_STOP = bytes((0x3F, 0x86))


@dataclass(frozen=True)
class Scd41Sample:
    co2_ppm: int
    temperature_c: float
    humidity_pct: float


def parse_measurement(frame: bytes) -> Scd41Sample:
    if len(frame) != 9:
        raise ValueError("SCD41 frame must be 9 bytes")
    check_word(frame[0:2], frame[2])
    check_word(frame[3:5], frame[5])
    check_word(frame[6:8], frame[8])
    co2 = int.from_bytes(frame[0:2], "big")
    temperature = round(-45 + 175 * int.from_bytes(frame[3:5], "big") / 65535, 2)
    humidity = round(100 * int.from_bytes(frame[6:8], "big") / 65535, 2)
    return Scd41Sample(co2_ppm=co2, temperature_c=temperature, humidity_pct=humidity)


def _ready(frame: bytes) -> bool:
    check_word(frame[0:2], frame[2])
    return (int.from_bytes(frame[0:2], "big") & 0x07FF) != 0


class Scd41:
    def __init__(self, bus: I2CBus, address: int = ADDRESS) -> None:
        self.bus = bus
        self.address = address
        self._started = False

    def start(self) -> None:
        self.bus.write(self.address, _START)
        self._started = True

    def read(self) -> Scd41Sample | None:
        if not self._started:
            self.start()
        ready = self.bus.write_read(self.address, _DATA_READY, 3)
        if not _ready(ready):
            return None
        frame = self.bus.write_read(self.address, _READ, 9)
        return parse_measurement(frame)

    def close(self) -> None:
        if self._started:
            try:
                self.bus.write(self.address, _STOP)
            except OSError:
                pass
            self._started = False
