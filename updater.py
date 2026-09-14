"""Velopack integration for background checks and user-approved updates."""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Callable


DEFAULT_VERSION = "1.0.0"


def _resource_file(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_update_config() -> dict:
    try:
        with open(_resource_file("update_config.json"), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run_startup_hooks() -> None:
    """Let Velopack process install/update command-line hooks before UI startup."""
    try:
        import velopack
        velopack.App().run()
    except ImportError:
        pass  # Development from source does not require the optional updater SDK.


class UpdateService:
    def __init__(self, emit: Callable[[dict], None]):
        self._emit = emit
        self._lock = threading.Lock()
        self._manager = None
        self._update_info = None
        self._busy = False
        self._config = load_update_config()
        self._state = {
            "status": "idle",
            "configured": bool((self._config.get("url") or "").strip()),
            "currentVersion": str(self._config.get("version") or DEFAULT_VERSION),
            "latestVersion": "",
            "releaseNotes": "",
            "progress": 0,
            "message": "",
        }
        self._initialise_manager()

    def _initialise_manager(self) -> None:
        url = (self._config.get("url") or "").strip()
        if not url:
            self._state["message"] = "Update feed is not configured in this build."
            return
        try:
            import velopack
            source_type = (self._config.get("source") or "http").strip().lower()
            if source_type == "github":
                source = velopack.GithubSource(url, None, bool(self._config.get("prerelease", False)))
            else:
                # A plain HTTPS URL selects Velopack's static web source. This
                # avoids GitHub's anonymous REST API (60 requests/hour/IP).
                source = url
            manager = velopack.UpdateManager(source)
            current = manager.get_current_version()
            if current:
                self._state["currentVersion"] = str(current)
            self._manager = manager
        except ImportError:
            self._state["message"] = "Updater support is unavailable in this development build."
        except Exception as exc:  # Velopack reports portable/unmanaged builds here.
            self._state["message"] = f"Updater is not active: {exc}"

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **changes) -> dict:
        with self._lock:
            self._state.update(changes)
            snapshot = dict(self._state)
        try:
            self._emit(snapshot)
        except Exception:
            pass
        return snapshot

    def check(self, manual: bool = True) -> dict:
        if not self._state["configured"]:
            return self._set(status="disabled", message="Update feed is not configured in this build.")
        if self._manager is None:
            return self._set(status="error", message=self._state["message"] or "Updater is unavailable.")
        with self._lock:
            if self._busy:
                return dict(self._state)
            self._busy = True
        self._set(status="checking", message="Checking for updates…", progress=0, quiet=not manual)

        def worker():
            try:
                info = self._manager.check_for_updates()
                self._update_info = info
                if not info:
                    changes = dict(status="up_to_date", latestVersion="", releaseNotes="",
                                   message="You are using the latest version.")
                else:
                    asset = info.TargetFullRelease
                    changes = dict(status="available", latestVersion=str(asset.Version),
                                   releaseNotes=str(asset.NotesMarkdown or ""),
                                   message=f"Version {asset.Version} is available.")
            except Exception as exc:
                # Automatic checks stay quiet in the UI; their state is still visible in Settings.
                changes = dict(status="error", message=f"Could not check for updates: {exc}",
                               quiet=not manual)
            finally:
                with self._lock:
                    self._busy = False
            self._set(**changes)

        threading.Thread(target=worker, name="update-check", daemon=True).start()
        return self.state()

    def download(self) -> dict:
        if self._manager is None or self._update_info is None:
            return self._set(status="error", message="Check for an update before downloading.")
        with self._lock:
            if self._busy:
                return dict(self._state)
            self._busy = True
        self._set(status="downloading", message="Downloading update…", progress=0)

        def progress(value):
            try:
                percent = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                percent = 0
            self._set(status="downloading", progress=percent,
                      message=f"Downloading update… {percent}%")

        def worker():
            try:
                self._manager.download_updates(self._update_info, progress)
                changes = dict(status="ready", progress=100,
                               message="Update downloaded. Restart to finish installing it.")
            except Exception as exc:
                changes = dict(status="error", message=f"Could not download the update: {exc}")
            finally:
                with self._lock:
                    self._busy = False
            self._set(**changes)

        threading.Thread(target=worker, name="update-download", daemon=True).start()
        return self.state()

    def apply_and_restart(self) -> dict:
        if self._manager is None or self._update_info is None or self._state.get("status") != "ready":
            return self._set(status="error", message="Download the update before restarting.")
        self._set(status="installing", message="Closing the app and installing the update…")
        try:
            # Update.exe waits for this process to release WebView2 and then relaunches it.
            self._manager.wait_exit_then_apply_updates(self._update_info, False, True, None)
            return self.state()
        except Exception as exc:
            return self._set(status="error", message=f"Could not start the installer: {exc}")
