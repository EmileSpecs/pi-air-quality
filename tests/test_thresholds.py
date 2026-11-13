"""AirQ Pro colour bands."""

from piair.thresholds import band_for


def test_bands_match_airq_pro() -> None:
    assert band_for("co2", 799) == "green"
    assert band_for("co2", 800) == "yellow"
    assert band_for("co2", 1000) == "amber"
    assert band_for("co2", 1500) == "red"
    assert band_for("pm25", 15) == "yellow"
    assert band_for("pm10", 45) == "yellow"
    assert band_for("formaldehyde", 40) == "yellow"
    assert band_for("voc_index", 149) == "green"
    assert band_for("nox_index", 20) == "yellow"
    assert band_for("pm1", None) is None
