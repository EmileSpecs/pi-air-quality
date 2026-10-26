"""PMSA003I and SCD41 frames, and what is allowed into the MQTT document."""

from piair.reading import Reading
from piair.sensors.crc import crc8
from piair.sensors.pmsa003i import parse_frame
from piair.sensors.scd41 import parse_measurement


def _pm_frame(pm1: int, pm25: int, pm10: int) -> bytes:
    data = bytearray(32)
    data[0] = 0x42
    data[1] = 0x4D
    data[2] = 0
    data[3] = 28

    def put(offset: int, value: int) -> None:
        data[offset] = (value >> 8) & 0xFF
        data[offset + 1] = value & 0xFF

    put(4, 1)
    put(6, 2)
    put(8, 3)
    put(10, pm1)
    put(12, pm25)
    put(14, pm10)
    checksum = sum(data[:30]) & 0xFFFF
    data[30] = (checksum >> 8) & 0xFF
    data[31] = checksum & 0xFF
    return bytes(data)


def _word(value: int) -> bytes:
    raw = value.to_bytes(2, "big")
    return raw + bytes((crc8(raw),))


def test_particulate_frame_uses_atmospheric_values() -> None:
    sample = parse_frame(_pm_frame(4, 9, 12))
    assert sample.pm1 == 4
    assert sample.pm25 == 9
    assert sample.pm10 == 12


def test_particulate_checksum_is_required() -> None:
    frame = bytearray(_pm_frame(1, 1, 1))
    frame[31] ^= 0xFF
    try:
        parse_frame(bytes(frame))
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("expected a checksum error")


def test_co2_frame_parses_and_is_the_only_published_field() -> None:
    # 800 ppm, plus temperature and humidity words the publisher must ignore.
    frame = _word(800) + _word(30000) + _word(20000)
    sample = parse_measurement(frame)
    assert sample.co2_ppm == 800
    assert sample.temperature_c != 0
    reading = Reading(co2_ppm=sample.co2_ppm, version="abc")
    state = reading.public_state(formaldehyde=False, particulate=False, co2=True)
    assert state["co2_ppm"] == 800
    assert "temperature_c" not in state
    assert "humidity_pct" not in state


def test_warming_state_omits_formaldehyde() -> None:
    reading = Reading(
        formaldehyde_ppb=0,
        temperature_c=21.5,
        humidity_pct=40.0,
        formaldehyde_status="warming_up",
        version="abc",
    )
    state = reading.public_state(formaldehyde=True, particulate=False, co2=False)
    assert "formaldehyde_ppb" not in state
    assert state["temperature_c"] == 21.5
    assert state["humidity_pct"] == 40.0
    assert state["formaldehyde_status"] == "warming_up"
