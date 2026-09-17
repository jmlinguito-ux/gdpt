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


def app_version() -> str:
    """Version of this build, shown in the window title and the top bar."""
    return str(load_update_config().get("version") or DEFAULT_VERSION)


def _clear_icon_cache() -> None:
    """Delete Windows icon-cache DB files and restart Explorer.

    Windows caches taskbar/shortcut icons in per-user DB files and does not
    always refresh them when the underlying EXE is replaced by an update.
    Deleting the cache and restarting Explorer forces an immediate rebuild so
    the new icon appears in the taskbar and on the Desktop shortcut.

    The operation is best-effort: any individual failure is silently ignored
    so that a missing file or a race with Explorer does not abort the hook.
    Only runs on Windows.
    """
    if os.name != "nt":
        return
    import glob
    import subprocess
    import time

    # 1. Stop Explorer so the cache files are not locked.
    subprocess.call(["taskkill", "/F", "/IM", "explorer.exe"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)

    # 2. Delete the icon-cache databases.
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local_appdata, "IconCache.db"),
        *glob.glob(os.path.join(local_appdata,
                                "Microsoft", "Windows", "Explorer",
                                "iconcache_*.db")),
    ]
    for path in targets:
        try:
            os.remove(path)
        except OSError:
            pass

    # 3. Restart Explorer (it restores the taskbar and desktop).
    subprocess.Popen(["explorer.exe"])


def run_startup_hooks() -> None:
    """Let Velopack process install/update command-line hooks before UI startup."""
    try:
        import velopack

        def _on_install(_version: str) -> None:
            _clear_icon_cache()

        def _on_updated(_version: str) -> None:
            _clear_icon_cache()

        velopack.App().on_first_run(_on_install).on_restarted(_on_updated).run()
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
        is_configured = bool((self._config.get("url") or "").strip())
        self._state = {
            "status": "up_to_date" if is_configured else "idle",
            "configured": is_configured,
            # False for builds Velopack cannot manage (running from source, the
            # PyInstaller dist folder, or a portable copy). Not an error state:
            # those builds are updated by reinstalling, not in-app.
            "active": False,
            "currentVersion": str(self._config.get("version") or DEFAULT_VERSION),
            "latestVersion": "",
            "releaseNotes": "",
            "progress": 0,
            "message": "Up to date" if is_configured else "",
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
                # Wrap the feed URL in HttpSource explicitly so Velopack uses
                # its static web source. A bare string is auto-detected as a
                # GitHub API source whenever the host is github.com, which
                # rewrites the feed path into an invalid REST URL and fails
                # with a 404. HttpSource also avoids GitHub's anonymous REST
                # API limit (60 requests/hour/IP).
                source = velopack.HttpSource(url)
            manager = velopack.UpdateManager(source)
            current = manager.get_current_version()
            if current:
                self._state["currentVersion"] = str(current)
            self._manager = manager
            self._state["active"] = True
        except ImportError:
            self._state["message"] = "Updater support is not bundled in this development build."
        except Exception:  # Velopack reports portable/unmanaged builds here.
            self._state["message"] = (
                "This build is not an installed release, so in-app updates are disabled."
            )

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
            # Not an error: this build simply cannot update itself in place.
            return self._set(status="disabled",
                             message=self._state["message"] or "Updates are unavailable in this build.")
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
                                   message="You are running the latest version.")
                else:
                    asset = info.TargetFullRelease
                    latest_ver = str(asset.Version)
                    cur_ver = self._state.get("currentVersion") or ""
                    if latest_ver and latest_ver.lstrip("v") == cur_ver.lstrip("v"):
                        changes = dict(status="up_to_date", latestVersion="", releaseNotes="",
                                       message="You are running the latest version.")
                    else:
                        changes = dict(status="available", latestVersion=latest_ver,
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
            self._manager.wait_exit_then_apply_updates(self._update_info, True, True, None)
            return self.state()
        except Exception as exc:
            return self._set(status="error", message=f"Could not start the installer: {exc}")
