"""SEN69C frame scaling, unknown sentinels, and command timing."""

from piair.sensors.crc import crc8
from piair.sensors.sen69c import Sen69c, parse_measurement


def _word(value: int, *, signed: bool = False) -> bytes:
    raw = value.to_bytes(2, "big", signed=signed)
    return raw + bytes((crc8(raw),))


def _frame(
    pm1: int = 30,
    pm25: int = 80,
    pm4: int = 90,
    pm10: int = 100,
    humidity: int = 4500,
    temperature: int = 4500,
    voc: int = 1000,
    nox: int = 10,
    hcho: int = 123,
    co2: int = 640,
) -> bytes:
    return b"".join(
        (
            _word(pm1),
            _word(pm25),
            _word(pm4),
            _word(pm10),
            _word(humidity, signed=True),
            _word(temperature, signed=True),
            _word(voc, signed=True),
            _word(nox, signed=True),
            _word(hcho),
            _word(co2, signed=True),
        )
    )


def test_ready_reading_uses_datasheet_scaling() -> None:
    sample = parse_measurement(_frame())
    assert sample.pm1 == 3.0
    assert sample.pm25 == 8.0
    assert sample.pm4 == 9.0
    assert sample.pm10 == 10.0
    assert sample.humidity_pct == 45.0
    assert sample.temperature_c == 22.5
    assert sample.voc_index == 100.0
    assert sample.nox_index == 1.0
    assert sample.formaldehyde_ppb == 12.3
    assert sample.co2_ppm == 640
    assert sample.formaldehyde_status == "ready"


def test_unknown_values_become_none_and_hcho_warms_up() -> None:
    sample = parse_measurement(
        _frame(
            pm1=0xFFFF,
            humidity=0x7FFF,
            temperature=0x7FFF,
            voc=0x7FFF,
            nox=0x7FFF,
            hcho=0xFFFF,
            co2=0x7FFF,
        )
    )
    assert sample.pm1 is None
    assert sample.humidity_pct is None
    assert sample.temperature_c is None
    assert sample.voc_index is None
    assert sample.nox_index is None
    assert sample.formaldehyde_ppb is None
    assert sample.co2_ppm is None
    assert sample.formaldehyde_status == "warming_up"
    assert sample.pm25 == 8.0


def test_bad_checksum_is_rejected() -> None:
    frame = bytearray(_frame())
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
        self.delay_reads: list[tuple[int, bytes, int, float]] = []

    def write(self, address: int, data: bytes) -> None:
        self.writes.append((address, bytes(data)))

    def write_read(self, address: int, data: bytes, length: int) -> bytes:
        raise AssertionError("SEN69C must use write_delay_read")

    def write_delay_read(self, address: int, data: bytes, length: int, delay_s: float) -> bytes:
        self.delay_reads.append((address, bytes(data), length, delay_s))
        assert length == len(self.frame)
        return self.frame


def test_start_and_read_use_sen69c_commands(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("piair.sensors.sen69c.time.sleep", sleeps.append)
    bus = _Bus(_frame())
    sensor = Sen69c(bus)
    sensor.start()
    sample = sensor.read()
    assert bus.writes[0] == (0x6B, bytes((0x00, 0x21)))
    assert sleeps[0] == 0.05
    assert bus.delay_reads[0] == (0x6B, bytes((0x04, 0xB5)), 30, 0.02)
    assert sample.co2_ppm == 640
