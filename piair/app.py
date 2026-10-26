"""Process wiring: sensors, MQTT, and the LAN pages."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from aiohttp import web

from piair.config import AirConfig, ConfigStore
from piair.diag import git_commit, local_ip
from piair.mqtt.aio import AioTransport
from piair.mqtt.bridge import MqttBridge
from piair.mqtt.discovery import DiscoveryContext
from piair.mqtt.memory import MemoryTransport
from piair.reading import Reading
from piair.sensors.hub import SensorHub, SimulatedHub
from piair.state import StateStore
from piair.update_cmd import spawn_update
from piair.web.server import create_app

log = logging.getLogger("piair")


def _blank(version: str) -> Reading:
    return Reading(version=version, formaldehyde_status="missing")


class App:
    def __init__(
        self,
        config: AirConfig,
        *,
        simulate: bool,
        config_path: Path,
        secrets_path: Path,
        state_path: Path,
    ) -> None:
        self.store = ConfigStore(config_path, secrets_path)
        self.store.config = config
        self.state = StateStore(state_path)
        self.simulate = simulate
        self.version = git_commit()
        self.reading = _blank(self.version)
        self.hub: SensorHub | SimulatedHub = SimulatedHub(config) if simulate else SensorHub(config)
        context = DiscoveryContext(ip=local_ip(config.mqtt.host), support_url=config.support_url())
        transport = MemoryTransport() if simulate else AioTransport(config)
        self.bridge = MqttBridge(
            config,
            transport,
            self.state,
            context=context,
            on_update=self._request_update,
        )
        self._runner: web.AppRunner | None = None
        self._last_status = ""
        self._last_publish = 0.0

    async def run(self) -> None:
        logging.basicConfig(
            level=self.store.config.logging.level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        await asyncio.to_thread(self.hub.open)
        await self._serve()
        await self.bridge.start()
        try:
            await self._sample_loop()
        finally:
            await self.bridge.stop()
            if self._runner is not None:
                await self._runner.cleanup()
            self.hub.close()

    async def _serve(self) -> None:
        app = create_app(
            self.store,
            snapshot=self.snapshot,
            on_saved=self._on_saved,
            on_update=self._request_update,
            device_locked=lambda: self.state.device_id_locked,
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        admin = self.store.config.admin
        site = web.TCPSite(self._runner, admin.bind, admin.port)
        await site.start()
        log.info("listening on %s:%s", admin.bind, admin.port)

    async def _sample_loop(self) -> None:
        interval = self.store.config.sensors.interval_s
        while True:
            reading = await asyncio.to_thread(self.hub.read)
            reading.version = self.version
            self.reading = reading
            now = time.monotonic()
            due = now - self._last_publish >= self.store.config.sensors.publish_interval_s
            changed = reading.formaldehyde_status != self._last_status
            if due or changed:
                await self.bridge.publish_state(reading)
                self._last_status = reading.formaldehyde_status
                self._last_publish = now
            await asyncio.sleep(interval)
            interval = self.store.config.sensors.interval_s

    async def _on_saved(self) -> None:
        config = self.store.config
        self.hub.config = config
        reconnect = self.bridge.update_config(config)
        if not reconnect:
            await self.bridge.publish_discovery()
            await self.bridge.publish_state(self.reading)

    async def _request_update(self) -> bool:
        return await asyncio.to_thread(spawn_update)

    def snapshot(self) -> dict[str, object]:
        reading = self.reading
        sensors = self.store.config.sensors

        def show(value: object, suffix: str) -> str:
            if value is None:
                return "—"
            return f"{value}{suffix}"

        return {
            "formaldehyde": show(reading.formaldehyde_ppb, " ppb"),
            "formaldehyde_status": reading.formaldehyde_status,
            "temperature": show(reading.temperature_c, " °C"),
            "humidity": show(reading.humidity_pct, " %"),
            "pm1": show(reading.pm1, " µg/m³"),
            "pm25": show(reading.pm25, " µg/m³"),
            "pm10": show(reading.pm10, " µg/m³"),
            "co2": show(reading.co2_ppm, " ppm"),
            "particulate_enabled": sensors.particulate.enabled,
            "co2_enabled": sensors.co2.enabled,
            "mqtt": "connected" if self.bridge.connected else "disconnected",
            "mqtt_error": self.bridge._last_error,
            "updated": reading.updated or "—",
            "version": self.version or "unknown",
            "detail": reading.detail,
        }
