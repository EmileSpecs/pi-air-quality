"""MQTT connect publishes discovery and the update button is not a retained command."""

import asyncio
import json

from piair.config import AirConfig, DeviceConfig, MqttConfig
from piair.mqtt.bridge import MqttBridge
from piair.mqtt.memory import Incoming, MemoryTransport
from piair.reading import Reading
from piair.state import StateStore


def _config() -> AirConfig:
    return AirConfig(
        device=DeviceConfig(id="lounge_air", name="Lounge air"),
        mqtt=MqttConfig(host="broker.local", username="kiosk", password="secret"),
    )


def test_connect_locks_device_and_publishes(tmp_path) -> None:
    async def scenario() -> None:
        transport = MemoryTransport()
        store = StateStore(tmp_path / "state.json")
        updates: list[str] = []

        async def on_update() -> None:
            updates.append("pressed")

        bridge = MqttBridge(_config(), transport, store, on_update=on_update)
        bridge.reading = Reading(
            formaldehyde_ppb=12.0,
            temperature_c=22.5,
            humidity_pct=45.0,
            formaldehyde_status="ready",
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
        await bridge._on_message(Incoming("piair/lounge_air/cmd/update", "PRESS", retain=True))
        assert updates == []
        await bridge._on_message(Incoming("piair/lounge_air/cmd/update", "PRESS", retain=False))
        assert updates == ["pressed"]
        await bridge._on_message(Incoming("homeassistant/status", "online"))
        assert any(item.topic.endswith("/formaldehyde/config") for item in transport.published)

    asyncio.run(scenario())
