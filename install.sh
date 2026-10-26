#!/usr/bin/env bash
# Idempotent installer. Run as root: sudo ./install.sh [options]
set -euo pipefail

USER_NAME=kiosk
DEV=0
REPO=""
REF=main
FORCE=0
PREFIX=/opt/pi-air

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]
  --user NAME     Unix user (default: kiosk)
  --dev           Editable install from this checkout
  --repo URL      Clone this git URL into /opt/pi-air
  --ref NAME      Git branch or tag (default: main)
  --force         Continue when this is not a Pi 5 running Bookworm
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) USER_NAME=$2; shift 2 ;;
    --dev) DEV=1; shift ;;
    --repo) REPO=$2; shift 2 ;;
    --ref) REF=$2; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

warn() { echo "warning: $*" >&2; }

preflight() {
  local model="" codename="" arch
  arch=$(uname -m)
  if [[ -r /proc/device-tree/model ]]; then
    model=$(tr -d '\0' < /proc/device-tree/model)
  fi
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    codename=${VERSION_CODENAME:-}
  fi
  if [[ "$model" != *"Raspberry Pi 5"* || "$codename" != "bookworm" || "$arch" != "aarch64" ]]; then
    warn "expected Raspberry Pi 5, Raspberry Pi OS Bookworm, 64-bit (got: ${model:-unknown}, ${codename:-unknown}, $arch)"
    if [[ "$FORCE" -ne 1 ]]; then
      echo "re-run with --force to continue" >&2
      exit 1
    fi
  fi
}

install_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y \
    python3-venv python3-dev python3-pip \
    i2c-tools git ca-certificates curl
}

ensure_user() {
  if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$USER_NAME"
  fi
  if getent group i2c >/dev/null 2>&1; then
    usermod -aG i2c "$USER_NAME" || true
  else
    warn "group i2c is not present yet; reboot after I2C is enabled, then re-run the installer"
  fi
  for group in systemd-journal adm; do
    if getent group "$group" >/dev/null 2>&1; then
      usermod -aG "$group" "$USER_NAME" || true
    fi
  done
  loginctl enable-linger "$USER_NAME"
}

enable_i2c() {
  local cfg="" changed=0 candidate
  for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "$candidate" ]]; then
      cfg=$candidate
      break
    fi
  done
  if [[ -z "$cfg" ]]; then
    warn "config.txt not found; enable I2C with raspi-config"
    return
  fi
  if ! grep -q '^dtparam=i2c_arm=on' "$cfg"; then
    printf '\n%s\n' 'dtparam=i2c_arm=on' >> "$cfg"
    changed=1
  fi
  if ! grep -q '^dtparam=i2c_arm_baudrate=' "$cfg"; then
    printf '%s\n' 'dtparam=i2c_arm_baudrate=100000' >> "$cfg"
    changed=1
  fi
  if [[ "$changed" -eq 1 ]]; then
    echo "I2C was enabled in $cfg. Reboot before the sensors will answer."
  fi
}

place_tree() {
  if [[ "$DEV" -eq 1 ]]; then
    PREFIX=$(cd "$(dirname "$0")" && pwd)
    return
  fi
  if [[ -n "$REPO" && ! -d "$PREFIX/.git" ]]; then
    git clone --branch "$REF" "$REPO" "$PREFIX"
  elif [[ -d "$PREFIX/.git" && -n "$REPO" ]]; then
    git -C "$PREFIX" -c safe.directory="$PREFIX" fetch origin "$REF" || warn "could not fetch $REF"
    git -C "$PREFIX" -c safe.directory="$PREFIX" pull --ff-only origin "$REF" || warn "could not fast-forward $REF"
  elif [[ ! -f "$PREFIX/pyproject.toml" ]]; then
    echo "no checkout at $PREFIX; pass --repo URL or --dev" >&2
    exit 1
  fi
  chown -R "$USER_NAME:$USER_NAME" "$PREFIX"
}

as_user() {
  sudo -u "$USER_NAME" bash -c 'cd "$1" && shift && exec "$@"' _ "$PREFIX" "$@"
}

install_venv() {
  if [[ ! -d "$PREFIX/venv" ]]; then
    as_user python3 -m venv "$PREFIX/venv"
  fi
  as_user "$PREFIX/venv/bin/pip" install -U pip
  if [[ -f "$PREFIX/requirements.lock" ]]; then
    as_user "$PREFIX/venv/bin/pip" install -r "$PREFIX/requirements.lock"
  fi
  if [[ "$DEV" -eq 1 ]]; then
    as_user "$PREFIX/venv/bin/pip" install -e "$PREFIX"
  else
    as_user "$PREFIX/venv/bin/pip" install "$PREFIX"
  fi
  chown -R "$USER_NAME:$USER_NAME" "$PREFIX/venv"
}

write_config() {
  install -d -o root -g "$USER_NAME" -m 2770 /etc/pi-air
  if [[ ! -f /etc/pi-air/config.yaml ]]; then
    cp "$PREFIX/config/config.example.yaml" /etc/pi-air/config.yaml
    chown root:"$USER_NAME" /etc/pi-air/config.yaml
    chmod 0660 /etc/pi-air/config.yaml
    if [[ -n "$REPO" ]]; then
      "$PREFIX/venv/bin/python" - "$REPO" <<'PY'
import sys
from pathlib import Path
path = Path("/etc/pi-air/config.yaml")
text = path.read_text(encoding="utf-8")
lines = []
for line in text.splitlines():
    if line.startswith("  repo:"):
        lines.append(f'  repo: "{sys.argv[1]}"')
    else:
        lines.append(line)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    fi
  else
    echo "keeping existing /etc/pi-air/config.yaml"
  fi
  if [[ ! -f /etc/pi-air/secrets.yaml ]]; then
    cp "$PREFIX/config/secrets.example.yaml" /etc/pi-air/secrets.yaml
  fi
  chown root:"$USER_NAME" /etc/pi-air/config.yaml /etc/pi-air/secrets.yaml
  chmod 0660 /etc/pi-air/config.yaml /etc/pi-air/secrets.yaml
  password=$("$PREFIX/venv/bin/python" - <<'PY'
from pathlib import Path
import secrets
path = Path("/etc/pi-air/secrets.yaml")
text = path.read_text(encoding="utf-8")
line = ""
for raw in text.splitlines():
    if raw.startswith("admin_password:"):
        line = raw
        break
if "change-me" in line or not line:
    generated = secrets.token_urlsafe(12)
    lines = []
    replaced = False
    for raw in text.splitlines():
        if raw.startswith("admin_password:"):
            lines.append(f'admin_password: "{generated}"')
            replaced = True
        else:
            lines.append(raw)
    if not replaced:
        lines.append(f'admin_password: "{generated}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(generated)
PY
)
  if [[ -n "${password:-}" ]]; then
    echo "Admin password (shown once): $password"
    echo "Open http://<this-pi>:8092 after the service starts."
  fi
  install -d -o "$USER_NAME" -g "$USER_NAME" -m 0700 "/home/$USER_NAME/.local/state/pi-air"
}

install_units() {
  local dest home
  home=$(getent passwd "$USER_NAME" | cut -d: -f6)
  dest="$home/.config/systemd/user"
  install -d -o "$USER_NAME" -g "$USER_NAME" "$dest"
  local unit name
  for unit in "$PREFIX"/system/systemd/user/*; do
    name=$(basename "$unit")
    sed "s#/opt/pi-air#$PREFIX#g" "$unit" > "$dest/$name"
    chown "$USER_NAME:$USER_NAME" "$dest/$name"
  done
  local uid
  uid=$(id -u "$USER_NAME")
  run_user() {
    sudo -u "$USER_NAME" XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user "$@" || true
  }
  if [[ ! -S "/run/user/$uid/bus" ]]; then
    loginctl enable-linger "$USER_NAME" || true
    systemctl start "user@${uid}.service" || true
  fi
  run_user daemon-reload
  run_user enable piaird.service piair-update.timer
  run_user restart piaird.service
}

summary() {
  echo
  echo "Pi air quality installed at $PREFIX"
  echo "Config: /etc/pi-air/config.yaml"
  echo "Secrets: /etc/pi-air/secrets.yaml"
  echo "Status: http://<this-pi>:8092"
  echo "Logs: journalctl --user -u piaird -f"
  echo "Manual update: sudo -u $USER_NAME $PREFIX/bin/piair-update.sh"
  if [[ -x "$PREFIX/venv/bin/piaird" ]]; then
    if "$PREFIX/venv/bin/piaird" --check-config /etc/pi-air/config.yaml; then
      echo "config check: ok"
    else
      echo "config check failed" >&2
    fi
  fi
}

preflight
install_packages
ensure_user
enable_i2c
place_tree
install_venv
write_config
install_units
summary
