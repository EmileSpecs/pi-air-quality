"""Shared I2C access. smbus2 is imported only when a real bus is opened."""

from __future__ import annotations

import threading
from typing import Protocol


class I2CBus(Protocol):
    def write(self, address: int, data: bytes) -> None: ...

    def write_read(self, address: int, data: bytes, length: int) -> bytes: ...


class SmbusTransport:
    """One lock around /dev/i2c-N so the sensors take turns."""

    def __init__(self, bus_number: int) -> None:
        import smbus2

        self._smbus2 = smbus2
        self._bus = smbus2.SMBus(bus_number)
        self._lock = threading.Lock()

    def write(self, address: int, data: bytes) -> None:
        with self._lock:
            message = self._smbus2.i2c_msg.write(address, data)
            self._bus.i2c_rdwr(message)

    def write_read(self, address: int, data: bytes, length: int) -> bytes:
        with self._lock:
            write = self._smbus2.i2c_msg.write(address, data)
            read = self._smbus2.i2c_msg.read(address, length)
            self._bus.i2c_rdwr(write, read)
            return bytes(read)

    def close(self) -> None:
        self._bus.close()
