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
from piair.thresholds import band_for
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
            on_apply_co2_setup=self._apply_co2_setup,
            on_calibrate_co2=self._calibrate_co2,
            on_set_ambient_pressure=self._set_ambient_pressure,
            on_set_co2_target=self._set_co2_target,
        )
        self._runner: web.AppRunner | None = None
        self._last_status = ""
        self._last_publish = 0.0
        self._co2_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        logging.basicConfig(
            level=self.store.config.logging.level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        await asyncio.to_thread(self.hub.open)
        await self._serve()
        await self.bridge.start()
        self._co2_task = asyncio.create_task(self._co2_lifecycle())
        try:
            await self._sample_loop()
        finally:
            if self._co2_task is not None:
                self._co2_task.cancel()
                try:
                    await self._co2_task
                except asyncio.CancelledError:
                    pass
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

    async def _co2_lifecycle(self) -> None:
        if not self.store.config.sensors.sen69c.enabled:
            return
        delay = self.store.config.sensors.sen69c.co2_setup_delay_s
        await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(self.hub.configure_co2)
            self.reading.co2_calibration_status = self.hub.co2_calibration_status
            await self.bridge.publish_state(self.reading)
        except Exception as exc:
            log.warning("CO2 setup failed: %s", exc)
        while True:
            interval = self.store.config.sensors.sen69c.pressure_refresh_interval_s
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(self.hub.set_ambient_pressure)
            except Exception as exc:
                log.warning("ambient pressure refresh failed: %s", exc)

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

    async def _apply_co2_setup(self) -> None:
        if not self.store.config.sensors.sen69c.enabled:
            return
        try:
            await asyncio.to_thread(self.hub.configure_co2)
            self.reading.co2_calibration_status = self.hub.co2_calibration_status
            await self.bridge.publish_state(self.reading)
        except Exception as exc:
            log.warning("Apply CO2 setup failed: %s", exc)

    async def _calibrate_co2(self) -> None:
        if not self.store.config.sensors.sen69c.enabled:
            return
        log.warning(
            "Calibrate CO2: leave the sensor outdoors in fresh air for at least 3 minutes first"
        )
        try:
            await asyncio.to_thread(self.hub.calibrate_co2)
            self.reading.co2_calibration_status = self.hub.co2_calibration_status
            await self.bridge.publish_state(self.reading)
        except Exception as exc:
            self.reading.co2_calibration_status = self.hub.co2_calibration_status or str(exc)
            await self.bridge.publish_state(self.reading)
            log.warning("Calibrate CO2 failed: %s", exc)

    async def _set_ambient_pressure(self, hpa: int) -> None:
        sen = self.store.config.sensors.sen69c.model_copy(update={"ambient_pressure_hpa": hpa})
        sensors = self.store.config.sensors.model_copy(update={"sen69c": sen})
        config = self.store.config.model_copy(update={"sensors": sensors})
        self.store.save(config, self.store.secrets)
        self.hub.config = self.store.config
        self.bridge.update_config(self.store.config)
        try:
            await asyncio.to_thread(self.hub.set_ambient_pressure)
        except Exception as exc:
            log.warning("Set ambient pressure failed: %s", exc)
        await self.bridge.publish_state(self.reading)

    async def _set_co2_target(self, ppm: int) -> None:
        sen = self.store.config.sensors.sen69c.model_copy(
            update={"co2_calibration_target_ppm": ppm}
        )
        sensors = self.store.config.sensors.model_copy(update={"sen69c": sen})
        config = self.store.config.model_copy(update={"sensors": sensors})
        self.store.save(config, self.store.secrets)
        self.hub.config = self.store.config
        self.bridge.update_config(self.store.config)
        await self.bridge.publish_state(self.reading)

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
            "formaldehyde_band": band_for("formaldehyde", reading.formaldehyde_ppb),
            "temperature": show(reading.temperature_c, " °C"),
            "humidity": show(reading.humidity_pct, " %"),
            "pm1": show(reading.pm1, " µg/m³"),
            "pm1_band": band_for("pm1", reading.pm1),
            "pm25": show(reading.pm25, " µg/m³"),
            "pm25_band": band_for("pm25", reading.pm25),
            "pm4": show(reading.pm4, " µg/m³"),
            "pm4_band": band_for("pm4", reading.pm4),
            "pm10": show(reading.pm10, " µg/m³"),
            "pm10_band": band_for("pm10", reading.pm10),
            "voc_index": show(reading.voc_index, ""),
            "voc_band": band_for("voc_index", reading.voc_index),
            "nox_index": show(reading.nox_index, ""),
            "nox_band": band_for("nox_index", reading.nox_index),
            "co2": show(reading.co2_ppm, " ppm"),
            "co2_band": band_for("co2", float(reading.co2_ppm) if reading.co2_ppm is not None else None),
            "co2_calibration_status": reading.co2_calibration_status or "—",
            "sen69c_enabled": sensors.sen69c.enabled,
            "formaldehyde_enabled": sensors.formaldehyde.enabled or sensors.sen69c.enabled,
            "mqtt": "connected" if self.bridge.connected else "disconnected",
            "mqtt_error": self.bridge._last_error,
            "updated": reading.updated or "—",
            "version": self.version or "unknown",
            "detail": reading.detail,
        }
