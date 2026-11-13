# Pi air quality

All channels from a Sensirion SEN69C on a Raspberry Pi 5 kiosk: particulate (PM1 / 2.5 / 4 / 10), temperature, humidity, VOC index, NOx index, formaldehyde, and CO2. An optional Sensirion SFA40 can still supply formaldehyde (and climate when the SEN69C is off). Readings are published to Home Assistant with MQTT discovery, the same way the video kiosk publishes its player. A small web page on port 8092 shows the live values and the broker settings. The video kiosk keeps the screen.

CO₂ engineering controls match the ESP32 AirQ Pro firmware: ASC off by default, Pretoria ambient pressure **865 hPa**, forced recalibration, and the same value colour bands on the status page. VOC and NOx are **indexes**, not concentrations in ppb.

## Wiring

One I2C bus, GPIO 2 (SDA, pin 3) and GPIO 3 (SCL, pin 5), plus 5 V (pin 2) and ground (pin 6).

| Sensor | Address | Power | Notes |
| --- | --- | --- | --- |
| SEN69C air quality | `0x6B` | 3.3–5 V | PM, climate, VOC, NOx, formaldehyde, CO2. |
| SFA40 formaldehyde (optional) | `0x5D` | 3.3–5 V | Use when you want the dedicated HCHO board. Leave the yellow membrane in place. |

The bus runs at 100 kHz. The installer enables `i2c_arm` if `config.txt` does not already, and asks for a reboot when it changes that file.

SEN69C formaldehyde is withheld for about the first minute after measurement starts (`0xFFFF` → status `warming_up`). CO2 and NOx also settle for a short time after start. With an SFA40 enabled, that board owns formaldehyde status instead.

### CO₂ setup (SEN69C)

ASC and ambient pressure are **volatile** on the SEN69C — they reset after power loss. About 15 seconds after open, `piaird` stops measurement, disables ASC, sets ambient pressure, and restarts measurement. Pressure is refreshed every hour while measuring.

Defaults (Pretoria ~1350 m):

```yaml
sensors:
  sen69c:
    enabled: true
    automatic_self_calibration: false
    ambient_pressure_hpa: 865
    co2_calibration_target_ppm: 420
    co2_setup_delay_s: 15
    pressure_refresh_interval_s: 3600
  formaldehyde:
    enabled: false
```

**Forced recalibration (FRC):** put the device outdoors in fresh air for at least **3 minutes**, then press **Calibrate CO2 now** in Home Assistant. Watch the logs / **CO2 calibration status** for `OK, correction … ppm` or `FAILED (0xFFFF)`.

Restart `piaird` after changing the sensors block. When both SEN69C and SFA40 are on, the SEN69C still publishes PM, climate, VOC, NOx, and CO2; the SFA40 owns formaldehyde.

## Install

Raspberry Pi 5, Raspberry Pi OS Bookworm 64-bit, and the Mosquitto broker Home Assistant already uses. MQTT without TLS is the default. This does not change the video kiosk.

```bash
ssh kiosk@kiosk.local 'curl -fsSL https://raw.githubusercontent.com/EmileSpecs/pi-air-quality/main/install.sh | sudo bash -s -- --repo https://github.com/EmileSpecs/pi-air-quality.git'
```

The checkout is `/opt/pi-air`. Config is created in `/etc/pi-air/` only when those files are missing. The installer prints an admin password once. Open `http://<pi>:8092`, sign in, and set the broker. The device id locks after the first successful MQTT connection.

A checkout you are working on can be installed in place:

```bash
sudo ./install.sh --dev --force
```

`--force` is only for machines that are not a Pi 5 running Bookworm.

On a PC, without hardware or a broker:

```bash
piaird --config config/config.example.yaml --simulate
```

## Update

Automatic update is on by default. Five minutes after boot, then every 15 minutes, `piair-update.timer` fast-forwards `update.ref` (usually `main`) from GitHub. It reinstalls `requirements.lock`, restarts `piaird`, and checks `http://127.0.0.1:8092/healthz`. A failed check resets the checkout to the previous commit. `/etc/pi-air` is never overwritten.

The status page and the Home Assistant **Update** button run the same script. Manual update:

```bash
sudo -u kiosk /opt/pi-air/bin/piair-update.sh
```

If you are already SSH’d in as another user:

```bash
sudo -u kiosk XDG_RUNTIME_DIR=/run/user/$(id -u kiosk) systemctl --user restart piaird.service
```

## Home Assistant

Discovery uses the `homeassistant` prefix. One device, `piair_<device id>`, with:

- PM1, PM2.5, PM4, and PM10 in µg/m³
- Temperature in °C and humidity in %
- VOC index and NOx index
- Formaldehyde in ppb, with status `warming_up`, `ready`, `missing`, or `error` (plus `stabilizing` when an SFA40 is used)
- CO2 in ppm
- **Ambient pressure** (number, 700–1200 hPa)
- **CO2 calibration target** (number, default 420 ppm)
- **Calibrate CO2 now** and **Apply CO2 setup** buttons
- CO2 calibration status (diagnostic)
- Version, and an Update button

Availability follows `piair/<device id>/status` (`online` / `offline`). The retained reading is `piair/<device id>/state`.

## Status colours

The admin status page colours readings with the same bands as AirQ Pro (value ≥ threshold enters that band):

| Channel | Yellow | Amber | Red |
| --- | --- | --- | --- |
| PM1 / PM2.5 / PM4 | 15 | 25 | 55 µg/m³ |
| PM10 | 45 | 80 | 150 µg/m³ |
| CO2 | 800 | 1000 | 1500 ppm |
| HCHO | 40 | 61 | 100 ppb |
| VOC index | 150 | 250 | 400 |
| NOx index | 20 | 50 | 150 |
