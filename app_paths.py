"""Stable application paths which survive Velopack upgrades."""

from __future__ import annotations

import os
import shutil
import sys


APP_ID = "GroundDataProcessingTool"


def install_dir() -> str:
    """Directory containing the executable (or source tree in development)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def user_data_dir() -> str:
    """Writable, version-independent storage for settings and authentication."""
    if not getattr(sys, "frozen", False):
        return install_dir()
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local, APP_ID)


def ensure_user_data() -> str:
    path = user_data_dir()
    os.makedirs(path, exist_ok=True)
    if getattr(sys, "frozen", False):
        _migrate_legacy_data(path)
    return path


def data_file(name: str) -> str:
    return os.path.join(ensure_user_data(), name)


def _migrate_legacy_data(destination: str) -> None:
    """Copy data from old portable builds once; never overwrite newer user data."""
    sources = [install_dir()]
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled and os.path.normcase(bundled) != os.path.normcase(sources[0]):
        sources.append(bundled)
    for name in ("app_settings.json", "dataverse_config.json", ".dv_token_cache.bin"):
        new_path = os.path.join(destination, name)
        if os.path.exists(new_path):
            continue
        for source in sources:
            if os.path.normcase(source) == os.path.normcase(destination):
                continue
            candidates = [name]
            if name == "dataverse_config.json":
                candidates.append("dataverse_config.example.json")
            copied = False
            for candidate in candidates:
                old_path = os.path.join(source, candidate)
                if os.path.isfile(old_path):
                    try:
                        shutil.copy2(old_path, new_path)
                    except OSError:
                        pass
                    copied = True
                    break
            if copied:
                break
