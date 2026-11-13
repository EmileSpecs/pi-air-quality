"""Discovery document for the SEN69C device."""

import json
from pathlib import Path

from piair.config import load_config
from piair.mqtt.discovery import DiscoveryContext, build_component_payloads

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "discovery" / "sen69c.json"
EXAMPLE = ROOT / "config" / "config.example.yaml"


def test_sen69c_discovery_matches_golden(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    secrets = tmp_path / "secrets.yaml"
    config_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    secrets.write_text('mqtt_password: "x"\nadmin_password: "y"\n', encoding="utf-8")
    config = load_config(config_path)
    payloads = build_component_payloads(
        config,
        DiscoveryContext(ip="192.168.1.20", sw_version="1.0.0"),
    )
    rendered = json.loads(json.dumps(payloads, sort_keys=True))
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert rendered == expected
    formaldehyde = payloads["homeassistant/sensor/piair_lounge_air/formaldehyde/config"]
    assert formaldehyde["unit_of_measurement"] == "ppb"
    assert "device_class" not in formaldehyde
    assert formaldehyde["device"]["configuration_url"] == "http://192.168.1.20:8092"
    assert "homeassistant/sensor/piair_lounge_air/pm4/config" in payloads
    assert "homeassistant/sensor/piair_lounge_air/voc_index/config" in payloads
    assert "homeassistant/sensor/piair_lounge_air/co2/config" in payloads
    assert "homeassistant/number/piair_lounge_air/ambient_pressure/config" in payloads
    assert "homeassistant/number/piair_lounge_air/co2_calibration_target/config" in payloads
    assert "homeassistant/button/piair_lounge_air/calibrate_co2/config" in payloads
    assert "homeassistant/button/piair_lounge_air/apply_co2_setup/config" in payloads
    assert "homeassistant/button/piair_lounge_air/update/config" in payloads

