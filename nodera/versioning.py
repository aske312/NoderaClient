"""Product version and build identity are different things."""

import json
import os
import subprocess
import sys
from pathlib import Path

from nodera import __version__


def git_revision(root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def build_info():
    revision = (
        "" if getattr(sys, "frozen", False) else git_revision(Path(__file__).resolve().parents[1])
    )
    development = {"version": __version__, "build": revision or "no-commit", "revision": revision}
    if not getattr(sys, "frozen", False):
        return development
    try:
        path = Path(__file__).parent / "resources" / "build-info.json"
        info = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(info, dict)
            and info.get("version") == __version__
            and isinstance(info.get("build"), str)
        ):
            return info
    except (OSError, ValueError, TypeError):
        pass
    return development


def version_text():
    info = build_info()
    return f"v. {info['version']} build:{info['build']}"
