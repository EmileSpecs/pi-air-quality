"""Open the configured sensors and fold them into one reading."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from piair.config import AirConfig
from piair.reading import Reading
from piair.sensors.bus import I2CBus, SmbusTransport
from piair.sensors.sen69c import Sen69c, Sen69cSample
from piair.sensors.sfa40 import Sfa40, Sfa40Sample

log = logging.getLogger("piair.sensors")


class SimulatedHub:
    """Fixed ready readings for --simulate and for page development."""

    def __init__(self, config: AirConfig) -> None:
        self.config = config
        self.co2_calibration_status = ""

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self) -> Reading:
        now = datetime.now(timezone.utc).isoformat()
        sensors = self.config.sensors
        reading = Reading(updated=now, formaldehyde_status="disabled")
        if sensors.sen69c.enabled:
            reading.pm1 = 3.0
            reading.pm25 = 8.0
            reading.pm4 = 9.0
            reading.pm10 = 10.0
            reading.temperature_c = 22.5
            reading.humidity_pct = 45.0
            reading.voc_index = 100.0
            reading.nox_index = 1.0
            reading.co2_ppm = 640
            reading.co2_calibration_status = self.co2_calibration_status
            if not sensors.formaldehyde.enabled:
                reading.formaldehyde_ppb = 12.0
                reading.formaldehyde_status = "ready"
        if sensors.formaldehyde.enabled:
            reading.formaldehyde_ppb = 12.0
            reading.formaldehyde_status = "ready"
            if not sensors.sen69c.enabled:
                reading.temperature_c = 22.5
                reading.humidity_pct = 45.0
        return reading

    def configure_co2(self) -> None:
        self.co2_calibration_status = "setup applied (simulate)"
        log.info("ASC disabled")
        log.info(
            "Ambient pressure set to %s hPa",
            self.config.sensors.sen69c.ambient_pressure_hpa,
        )

    def set_ambient_pressure(self) -> None:
        log.info(
            "Ambient pressure set to %s hPa",
            self.config.sensors.sen69c.ambient_pressure_hpa,
        )

    def calibrate_co2(self) -> int:
        target = self.config.sensors.sen69c.co2_calibration_target_ppm
        log.info(
            "CO2 FRC starting (target %s ppm) — leave device in fresh outdoor air",
            target,
        )
        correction = 0
        self.co2_calibration_status = f"OK, correction {correction:+.0f} ppm (simulate)"
        log.info("Forced recalibration OK, correction %+.0f ppm", correction)
        return correction


class SensorHub:
    def __init__(self, config: AirConfig, bus: I2CBus | None = None) -> None:
        self.config = config
        self._bus = bus
        self._owns_bus = bus is None
        self._sen: Sen69c | None = None
        self._sfa: Sfa40 | None = None
        self._sfa_seen = False
        self._sen_seen = False
        self._opened = False
        self._lock = threading.Lock()
        self.co2_calibration_status = ""

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
        if sensors.sen69c.enabled:
            self._sen = Sen69c(self._bus)
            self._try_start(self._sen, "sen69c")
        if sensors.formaldehyde.enabled:
            self._sfa = Sfa40(self._bus)
            self._try_start(self._sfa, "formaldehyde")

    def close(self) -> None:
        with self._lock:
            for sensor in (self._sen, self._sfa):
                if sensor is not None:
                    sensor.close()
            if self._owns_bus and isinstance(self._bus, SmbusTransport):
                self._bus.close()

    def read(self) -> Reading:
        with self._lock:
            if not self._opened:
                self.open()
            sensors = self.config.sensors
            if sensors.formaldehyde.enabled or sensors.sen69c.enabled:
                status = "missing"
            else:
                status = "disabled"
            reading = Reading(
                formaldehyde_status=status,
                co2_calibration_status=self.co2_calibration_status,
            )
            if self._sen is None and sensors.sen69c.enabled:
                reading.detail = "sen69c sensor was not opened"
            elif self._sen is not None:
                self._read_sen69c(reading, apply_hcho=not sensors.formaldehyde.enabled)
            if self._sfa is None and sensors.formaldehyde.enabled:
                reading.formaldehyde_status = "missing"
                if not reading.detail:
                    reading.detail = "formaldehyde sensor was not opened"
            elif self._sfa is not None:
                self._read_sfa(reading, apply_climate=not sensors.sen69c.enabled)
            reading.updated = datetime.now(timezone.utc).isoformat()
            return reading

    def configure_co2(self) -> None:
        with self._lock:
            if self._sen is None:
                raise RuntimeError("sen69c sensor was not opened")
            cfg = self.config.sensors.sen69c
            self._sen.configure_co2(
                cfg.ambient_pressure_hpa,
                asc_enabled=cfg.automatic_self_calibration,
            )
            self.co2_calibration_status = "setup applied"

    def set_ambient_pressure(self) -> None:
        with self._lock:
            if self._sen is None:
                raise RuntimeError("sen69c sensor was not opened")
            self._sen.set_ambient_pressure(self.config.sensors.sen69c.ambient_pressure_hpa)

    def calibrate_co2(self) -> int:
        with self._lock:
            if self._sen is None:
                raise RuntimeError("sen69c sensor was not opened")
            cfg = self.config.sensors.sen69c
            try:
                correction = self._sen.calibrate_co2(
                    cfg.co2_calibration_target_ppm,
                    cfg.ambient_pressure_hpa,
                )
            except RuntimeError:
                self.co2_calibration_status = "FAILED (0xFFFF)"
                raise
            self.co2_calibration_status = f"OK, correction {correction:+.0f} ppm"
            return correction

    def _try_start(self, sensor: Sen69c | Sfa40, name: str) -> None:
        try:
            sensor.start()
        except OSError as exc:
            log.warning("%s did not start: %s", name, exc)

    def _read_sen69c(self, reading: Reading, *, apply_hcho: bool) -> None:
        assert self._sen is not None
        try:
            sample = self._sen.read()
        except (OSError, ValueError) as exc:
            if apply_hcho:
                reading.formaldehyde_status = "error" if self._sen_seen else "missing"
            reading.detail = str(exc)
            log.warning("sen69c read failed: %s", exc)
            return
        self._apply_sen69c(reading, sample, apply_hcho=apply_hcho)
        self._sen_seen = True

    def _apply_sen69c(self, reading: Reading, sample: Sen69cSample, *, apply_hcho: bool) -> None:
        reading.pm1 = sample.pm1
        reading.pm25 = sample.pm25
        reading.pm4 = sample.pm4
        reading.pm10 = sample.pm10
        reading.temperature_c = sample.temperature_c
        reading.humidity_pct = sample.humidity_pct
        reading.voc_index = sample.voc_index
        reading.nox_index = sample.nox_index
        reading.co2_ppm = sample.co2_ppm
        if apply_hcho:
            reading.formaldehyde_ppb = sample.formaldehyde_ppb
            reading.formaldehyde_status = sample.formaldehyde_status
        reading.detail = ""

    def _read_sfa(self, reading: Reading, *, apply_climate: bool) -> None:
        assert self._sfa is not None
        try:
            sample = self._sfa.read()
        except (OSError, ValueError) as exc:
            reading.formaldehyde_status = "error" if self._sfa_seen else "missing"
            reading.detail = str(exc)
            log.warning("formaldehyde read failed: %s", exc)
            return
        self._apply_sfa(reading, sample, apply_climate=apply_climate)
        self._sfa_seen = True

    def _apply_sfa(self, reading: Reading, sample: Sfa40Sample, *, apply_climate: bool) -> None:
        reading.formaldehyde_ppb = sample.formaldehyde_ppb
        reading.formaldehyde_status = sample.status
        if apply_climate:
            reading.temperature_c = sample.temperature_c
            reading.humidity_pct = sample.humidity_pct
        reading.detail = ""
