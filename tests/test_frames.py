"""Public MQTT state filtering for SEN69C and formaldehyde warmup."""

from piair.reading import Reading


def test_sen69c_state_publishes_all_channels() -> None:
    reading = Reading(
        formaldehyde_ppb=12.3,
        temperature_c=22.5,
        humidity_pct=45.0,
        formaldehyde_status="ready",
        pm1=3.0,
        pm25=8.0,
        pm4=9.0,
        pm10=10.0,
        voc_index=100.0,
        nox_index=1.0,
        co2_ppm=640,
        version="abc",
    )
    state = reading.public_state(formaldehyde=False, sen69c=True)
    assert state["formaldehyde_ppb"] == 12.3
    assert state["pm4"] == 9.0
    assert state["voc_index"] == 100.0
    assert state["nox_index"] == 1.0
    assert state["co2_ppm"] == 640
    assert state["temperature_c"] == 22.5


def test_warming_state_omits_formaldehyde() -> None:
    reading = Reading(
        formaldehyde_ppb=0,
        temperature_c=21.5,
        humidity_pct=40.0,
        formaldehyde_status="warming_up",
        version="abc",
    )
    state = reading.public_state(formaldehyde=False, sen69c=True)
    assert "formaldehyde_ppb" not in state
    assert state["temperature_c"] == 21.5
    assert state["humidity_pct"] == 40.0
    assert state["formaldehyde_status"] == "warming_up"


def test_sfa_only_omits_sen69c_channels() -> None:
    reading = Reading(
        formaldehyde_ppb=8.0,
        temperature_c=21.0,
        humidity_pct=40.0,
        formaldehyde_status="ready",
        pm1=3.0,
        co2_ppm=500,
        version="abc",
    )
    state = reading.public_state(formaldehyde=True, sen69c=False)
    assert state["formaldehyde_ppb"] == 8.0
    assert "pm1" not in state
    assert "co2_ppm" not in state
