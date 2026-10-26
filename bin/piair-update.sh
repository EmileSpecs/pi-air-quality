#!/bin/sh
# Fast-forward the air-quality checkout, reinstall, and roll back if it does not come back.
set -eu
ROOT=${PIAIR_ROOT:-/opt/pi-air}
CONFIG=${PIAIR_CONFIG:-/etc/pi-air/config.yaml}
FROM_TIMER=0
if [ "${1:-}" = "--from-timer" ]; then
  FROM_TIMER=1
fi

cd "$ROOT"
PY=$ROOT/venv/bin/python
if [ ! -x "$PY" ]; then
  echo "missing $PY" >&2
  exit 1
fi
fields=$("$PY" - "$CONFIG" <<'PY'
import sys
from piair.config import load_config
config = load_config(sys.argv[1])
print(config.update.ref)
print("1" if config.update.auto else "0")
print(config.admin.port)
PY
)
ref=$(printf '%s\n' "$fields" | sed -n '1p')
auto=$(printf '%s\n' "$fields" | sed -n '2p')
port=$(printf '%s\n' "$fields" | sed -n '3p')

if [ "$FROM_TIMER" = "1" ] && [ "$auto" != "1" ]; then
  echo "auto update is off"
  exit 0
fi

gitcmd() {
  git -c safe.directory="$ROOT" "$@"
}

before=$(gitcmd rev-parse HEAD)
gitcmd fetch --prune origin
remote=$(gitcmd rev-parse "origin/$ref")
if [ "$remote" = "$before" ]; then
  echo "up to date $before"
  exit 0
fi

rollback() {
  echo "update failed; restoring $before" >&2
  gitcmd reset --hard "$before"
  "$ROOT/venv/bin/pip" install -r "$ROOT/requirements.lock"
  "$ROOT/venv/bin/pip" install "$ROOT"
  systemctl --user restart piaird.service || true
}

if ! gitcmd pull --ff-only origin "$ref"; then
  echo "fast-forward failed" >&2
  exit 1
fi
if ! "$ROOT/venv/bin/pip" install -r "$ROOT/requirements.lock"; then
  rollback
  exit 1
fi
if ! "$ROOT/venv/bin/pip" install "$ROOT"; then
  rollback
  exit 1
fi

unit_src=$ROOT/system/systemd/user
unit_dst=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
if [ -d "$unit_src" ]; then
  mkdir -p "$unit_dst"
  for unit in piaird.service piair-update.service piair-update.timer; do
    if [ -f "$unit_src/$unit" ]; then
      sed "s#/opt/pi-air#$ROOT#g" "$unit_src/$unit" > "$unit_dst/$unit"
    fi
  done
  systemctl --user daemon-reload || true
  systemctl --user enable piair-update.timer || true
fi

systemctl --user restart piaird.service
if ! "$ROOT/bin/wait-for-http" "http://127.0.0.1:${port}/healthz" 30; then
  rollback
  exit 1
fi
echo "updated $(gitcmd rev-parse HEAD)"
