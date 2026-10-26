"""Start the GitHub update script without waiting for it to restart us."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from piair.diag import install_root

log = logging.getLogger("piair.update")


def spawn_update(root: Path | None = None) -> bool:
    script = (root or install_root()) / "bin" / "piair-update.sh"
    if not script.is_file():
        log.warning("update script is not available at %s", script)
        return False
    kwargs: dict[str, object] = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    subprocess.Popen(  # noqa: S603 - fixed script path from the install root
        [str(script)],
        cwd=str(script.parent.parent),
        **kwargs,
    )
    log.info("update started")
    return True
