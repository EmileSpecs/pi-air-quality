"""Checkout location and the installed git revision."""

from __future__ import annotations

import os
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install_root() -> Path:
    env = os.environ.get("PIAIR_ROOT")
    if env:
        return Path(env)
    opt = Path("/opt/pi-air")
    if (opt / ".git").exists():
        return opt
    return ROOT


def local_ip(host: str) -> str:
    """Source address of a UDP socket aimed at the broker. Empty when offline."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(1)
        sock.connect((host, 80))
        address = sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()
    if address.startswith("127."):
        return ""
    return address


def git_commit(root: Path | None = None) -> str:
    """Short commit of the checkout without running git."""
    base = (root or install_root()) / ".git"
    try:
        head = (base / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head.startswith("ref:"):
        return head[:9]
    ref = head.split(":", 1)[1].strip()
    try:
        return (base / ref).read_text(encoding="utf-8").strip()[:9]
    except OSError:
        pass
    try:
        for line in (base / "packed-refs").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0][:9]
    except OSError:
        pass
    return ""
