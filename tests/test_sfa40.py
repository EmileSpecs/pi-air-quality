"""SFA40 scaling, status bits, and the command bytes from the datasheet."""

from piair.sensors.crc import crc8
from piair.sensors.sfa40 import Sfa40, parse_measurement, status_name


def _word(data: bytes) -> bytes:
    return data + bytes((crc8(data),))


def _frame(hcho: int, humidity: int, temperature: int, status: int) -> bytes:
    return b"".join(
        (
            _word(hcho.to_bytes(2, "big")),
            _word(humidity.to_bytes(2, "big")),
            _word(temperature.to_bytes(2, "big")),
            _word(bytes((status, 0))),
        )
    )


def test_crc_matches_datasheet_commands() -> None:
    assert crc8(b"\xbe\xef") == 0x92
    assert crc8(bytes([0x00])) == 0xAC
    assert crc8(bytes([0xC0])) == 0xEB
    assert crc8(bytes([0x50])) == 0xD2


def test_ready_reading_uses_datasheet_scaling() -> None:
    sample = parse_measurement(_frame(123, 32768, 32768, 0))
    assert sample.status == "ready"
    assert sample.formaldehyde_ppb == 12.3
    assert sample.humidity_pct == round(-6 + 125 * 32768 / 65535, 2)
    assert sample.temperature_c == round(-45 + 175 * 32768 / 65535, 2)


def test_warmup_hides_formaldehyde_but_keeps_climate() -> None:
    warming = parse_measurement(_frame(0, 20000, 18000, 0x01))
    assert warming.status == "warming_up"
    assert warming.formaldehyde_ppb is None
    assert warming.temperature_c is not None
    settling = parse_measurement(_frame(80, 20000, 18000, 0x02))
    assert settling.status == "stabilizing"
    assert settling.formaldehyde_ppb == 8.0
    assert status_name(0x03) == "warming_up"


def test_bad_checksum_is_rejected() -> None:
    frame = bytearray(_frame(10, 1000, 1000, 0))
    frame[2] ^= 0xFF
    try:
        parse_measurement(bytes(frame))
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("expected a checksum error")


class _Bus:
    def __init__(self, frame: bytes) -> None:
        self.frame = frame
        self.writes: list[tuple[int, bytes]] = []

    def write(self, address: int, data: bytes) -> None:
        self.writes.append((address, bytes(data)))

    def write_read(self, address: int, data: bytes, length: int) -> bytes:
        self.writes.append((address, bytes(data)))
        assert length == len(self.frame)
        return self.frame


def test_start_command_is_address_and_crc() -> None:
    bus = _Bus(_frame(10, 1000, 1000, 0))
    sensor = Sfa40(bus)
    sensor.start()
    sample = sensor.read()
    assert bus.writes[0] == (0x5D, bytes((0x00, 0xAC)))
    assert bus.writes[1] == (0x5D, bytes((0xC0, 0xEB)))
    assert sample.status == "ready"
