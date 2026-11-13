"""MQTT connect publishes discovery and the update button is not a retained command."""

import asyncio
import json

from piair.config import AirConfig, DeviceConfig, MqttConfig, Sen69cConfig, SensorsConfig
from piair.mqtt.bridge import MqttBridge
from piair.mqtt.memory import Incoming, MemoryTransport
from piair.reading import Reading
from piair.state import StateStore


def _config() -> AirConfig:
    return AirConfig(
        device=DeviceConfig(id="lounge_air", name="Lounge air"),
        mqtt=MqttConfig(host="broker.local", username="kiosk", password="secret"),
        sensors=SensorsConfig(sen69c=Sen69cConfig(enabled=True)),
    )


def test_connect_locks_device_and_publishes(tmp_path) -> None:
    async def scenario() -> None:
        transport = MemoryTransport()
        store = StateStore(tmp_path / "state.json")
        updates: list[str] = []
        calibrated: list[str] = []

        async def on_update() -> None:
            updates.append("pressed")

        async def on_calibrate() -> None:
            calibrated.append("frc")

        bridge = MqttBridge(
            _config(),
            transport,
            store,
            on_update=on_update,
            on_calibrate_co2=on_calibrate,
        )
        bridge.reading = Reading(
            formaldehyde_ppb=12.0,
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
            co2_calibration_status="setup applied",
            version="abc1234",
            updated="2026-10-26T12:00:00+00:00",
        )
        await bridge._on_connect()
        topics = [item.topic for item in transport.published]
        assert "piair/lounge_air/status" in topics
        assert transport.published[-1].payload == "online"
        assert store.device_id_locked is True
        state = json.loads(
            next(item.payload for item in transport.published if item.topic == "piair/lounge_air/state")
        )
        assert state["formaldehyde_ppb"] == 12.0
        assert state["pm4"] == 9.0
        assert state["co2_ppm"] == 640
        assert state["ambient_pressure_hpa"] == 865
        assert state["co2_calibration_target_ppm"] == 420
        await bridge._on_message(Incoming("piair/lounge_air/cmd/update", "PRESS", retain=True))
        assert updates == []
        await bridge._on_message(Incoming("piair/lounge_air/cmd/update", "PRESS", retain=False))
        assert updates == ["pressed"]
        await bridge._on_message(Incoming("piair/lounge_air/cmd/calibrate_co2", "PRESS", retain=False))
        assert calibrated == ["frc"]
        await bridge._on_message(Incoming("homeassistant/status", "online"))
        assert any(item.topic.endswith("/pm4/config") for item in transport.published)
        assert any(item.topic.endswith("/formaldehyde/config") for item in transport.published)
        assert any(item.topic.endswith("/ambient_pressure/config") for item in transport.published)
        assert any(item.topic.endswith("/calibrate_co2/config") for item in transport.published)

    asyncio.run(scenario())
