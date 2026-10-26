"""Sensirion CRC-8 (polynomial 0x31, initial value 0xFF)."""

from __future__ import annotations


def crc8(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def check_word(data: bytes, checksum: int) -> None:
    if crc8(data) != checksum:
        raise ValueError("checksum mismatch")
