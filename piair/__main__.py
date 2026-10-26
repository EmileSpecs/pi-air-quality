"""piaird entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from piair.app import App
from piair.config import ConfigError, load_config, secrets_path_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="piaird")
    parser.add_argument("--config", default=None)
    parser.add_argument("--check-config", metavar="PATH", default=None)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--state", default="state.json")
    args = parser.parse_args(argv)
    path = args.check_config or args.config
    if path is None:
        parser.error("pass --config PATH or --check-config PATH")
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.check_config:
        return 0
    app = App(
        config,
        simulate=args.simulate,
        config_path=Path(path),
        secrets_path=secrets_path_for(Path(path)),
        state_path=Path(args.state),
    )
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
