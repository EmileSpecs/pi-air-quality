# Pi air quality

Formaldehyde, temperature, and humidity from a Sensirion SFA40 on a Raspberry Pi 5 kiosk. The same process can read a PMSA003I particulate sensor and an SCD41 CO2 sensor when those are enabled. Readings are published to Home Assistant with MQTT discovery, the same way the video kiosk publishes its player. A small web page on port 8092 shows the live values and the broker settings. The video kiosk keeps the screen.

## Wiring

One I2C bus, GPIO 2 (SDA, pin 3) and GPIO 3 (SCL, pin 5), plus 5 V (pin 2) and ground (pin 6).

| Sensor | Address | Power | Notes |
| --- | --- | --- | --- |
| SEN0661 SFA40 formaldehyde | `0x5D` | 3.3–5 V | Also the room temperature and humidity. Leave the yellow membrane in place. |
| PMSA003I particles | `0x12` | 5 V, about 100 mA | 3.3 V I2C. Leave SET open or high. Do not hold RESET low. |
| SEN0536 SCD41 CO2 | `0x62` | 3.3–5 V | CO2 only. Its temperature and humidity are not published. |

The bus runs at 100 kHz. The installer enables `i2c_arm` if `config.txt` does not already, and asks for a reboot when it changes that file.

The formaldehyde value is withheld for the first minute after power-up. It is published while the sensor is still settling, and it is within specification after about 10 minutes.

Particulate and CO2 ship turned off:

```yaml
sensors:
  particulate:
    enabled: true
  co2:
    enabled: true
```

Restart `piaird` after changing that block.

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
ssh kiosk@kiosk.local 'sudo -u kiosk /opt/pi-air/bin/piair-update.sh'
```

## Home Assistant

Discovery uses the `homeassistant` prefix. One device, `piair_<device id>`, with:

- Formaldehyde in ppb
- Temperature in °C and humidity in % from the SFA40
- Formaldehyde status: `warming_up`, `stabilizing`, `ready`, `missing`, or `error`
- Version, and an Update button
- PM1, PM2.5, PM10, and CO2 when those sensors are enabled

Availability follows `piair/<device id>/status` (`online` / `offline`). The retained reading is `piair/<device id>/state`.
