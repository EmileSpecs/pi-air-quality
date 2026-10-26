"""Open the configured sensors and fold them into one reading."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from piair.config import AirConfig
from piair.reading import Reading
from piair.sensors.bus import I2CBus, SmbusTransport
from piair.sensors.pmsa003i import Pmsa003i
from piair.sensors.scd41 import Scd41, Scd41Sample
from piair.sensors.sfa40 import Sfa40, Sfa40Sample

log = logging.getLogger("piair.sensors")


class SimulatedHub:
    """Fixed ready readings for --simulate and for page development."""

    def __init__(self, config: AirConfig) -> None:
        self.config = config

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self) -> Reading:
        now = datetime.now(timezone.utc).isoformat()
        reading = Reading(updated=now, formaldehyde_status="disabled")
        if self.config.sensors.formaldehyde.enabled:
            reading.formaldehyde_ppb = 12.0
            reading.temperature_c = 22.5
            reading.humidity_pct = 45.0
            reading.formaldehyde_status = "ready"
        if self.config.sensors.particulate.enabled:
            reading.pm1 = 3
            reading.pm25 = 8
            reading.pm10 = 10
        if self.config.sensors.co2.enabled:
            reading.co2_ppm = 640
        return reading


class SensorHub:
    def __init__(self, config: AirConfig, bus: I2CBus | None = None) -> None:
        self.config = config
        self._bus = bus
        self._owns_bus = bus is None
        self._sfa: Sfa40 | None = None
        self._pm: Pmsa003i | None = None
        self._co2: Scd41 | None = None
        self._sfa_seen = False
        self._opened = False

    def open(self) -> None:
        if self._opened:
            return
        self._opened = True
        if self._bus is None:
            try:
                self._bus = SmbusTransport(self.config.sensors.bus)
            except OSError as exc:
                log.warning("i2c bus %s is unavailable: %s", self.config.sensors.bus, exc)
                return
        sensors = self.config.sensors
        if sensors.formaldehyde.enabled:
            self._sfa = Sfa40(self._bus)
            self._try_start(self._sfa, "formaldehyde")
        if sensors.particulate.enabled:
            self._pm = Pmsa003i(self._bus)
        if sensors.co2.enabled:
            self._co2 = Scd41(self._bus)
            self._try_start(self._co2, "co2")

    def close(self) -> None:
        for sensor in (self._sfa, self._pm, self._co2):
            if sensor is not None:
                sensor.close()
        if self._owns_bus and isinstance(self._bus, SmbusTransport):
            self._bus.close()

    def read(self) -> Reading:
        if not self._opened:
            self.open()
        reading = Reading(formaldehyde_status="missing" if self.config.sensors.formaldehyde.enabled else "disabled")
        if self._sfa is None and self.config.sensors.formaldehyde.enabled:
            reading.formaldehyde_status = "missing"
            reading.detail = "formaldehyde sensor was not opened"
        elif self._sfa is not None:
            self._read_sfa(reading)
        if self._pm is not None:
            self._read_pm(reading)
        if self._co2 is not None:
            self._read_co2(reading)
        reading.updated = datetime.now(timezone.utc).isoformat()
        return reading

    def _try_start(self, sensor: Sfa40 | Scd41, name: str) -> None:
        try:
            sensor.start()
        except OSError as exc:
            log.warning("%s did not start: %s", name, exc)

    def _read_sfa(self, reading: Reading) -> None:
        assert self._sfa is not None
        try:
            sample = self._sfa.read()
        except (OSError, ValueError) as exc:
            reading.formaldehyde_status = "error" if self._sfa_seen else "missing"
            reading.detail = str(exc)
            log.warning("formaldehyde read failed: %s", exc)
            return
        self._apply_sfa(reading, sample)
        self._sfa_seen = True

    def _apply_sfa(self, reading: Reading, sample: Sfa40Sample) -> None:
        reading.formaldehyde_ppb = sample.formaldehyde_ppb
        reading.temperature_c = sample.temperature_c
        reading.humidity_pct = sample.humidity_pct
        reading.formaldehyde_status = sample.status
        reading.detail = ""

    def _read_pm(self, reading: Reading) -> None:
        assert self._pm is not None
        try:
            sample = self._pm.read()
        except (OSError, ValueError) as exc:
            log.warning("particulate read failed: %s", exc)
            return
        reading.pm1 = sample.pm1
        reading.pm25 = sample.pm25
        reading.pm10 = sample.pm10

    def _read_co2(self, reading: Reading) -> None:
        assert self._co2 is not None
        try:
            sample = self._co2.read()
        except (OSError, ValueError) as exc:
            log.warning("co2 read failed: %s", exc)
            return
        if isinstance(sample, Scd41Sample):
            reading.co2_ppm = sample.co2_ppm
