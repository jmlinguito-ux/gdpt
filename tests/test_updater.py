import time
import unittest
import sys
from types import SimpleNamespace
from unittest import mock

from updater import UpdateService, load_update_config


class _Asset:
    Version = "1.1.0"
    NotesMarkdown = "Fixed spreadsheet export."


class _Info:
    TargetFullRelease = _Asset()


class _Manager:
    def __init__(self, available=True):
        self.available = available
        self.downloaded = False

    def check_for_updates(self):
        return _Info() if self.available else None

    def download_updates(self, info, callback):
        self.downloaded = True
        callback(25)
        callback(100)


def _wait_for(service, status, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = service.state()
        if state["status"] == status:
            return state
        time.sleep(0.01)
    raise AssertionError(f"Updater did not reach {status!r}: {service.state()}")


class UpdateServiceTests(unittest.TestCase):
    def make_service(self, manager):
        events = []
        service = UpdateService(events.append)
        service._state["configured"] = True
        service._manager = manager
        return service, events

    def test_check_finds_release_and_downloads(self):
        service, events = self.make_service(_Manager())
        service.check(True)
        state = _wait_for(service, "available")
        self.assertEqual(state["latestVersion"], "1.1.0")
        self.assertIn("spreadsheet export", state["releaseNotes"])

        service.download()
        state = _wait_for(service, "ready")
        self.assertEqual(state["progress"], 100)
        self.assertTrue(events)

    def test_check_reports_latest_version(self):
        service, _ = self.make_service(_Manager(available=False))
        service.check(True)
        state = _wait_for(service, "up_to_date")
        self.assertIn("latest", state["message"])

    def test_release_config_uses_static_feed_without_github_api(self):
        config = load_update_config()
        self.assertEqual(config["source"], "http")
        self.assertEqual(
            config["url"],
            "https://github.com/jmlinguito-ux/gdpt/releases/latest/download",
        )

    def test_http_source_is_passed_directly_to_update_manager(self):
        feed = "https://github.com/example/app/releases/latest/download"
        seen = []

        class Manager:
            def __init__(self, source):
                seen.append(source)

            def get_current_version(self):
                return "1.0.1"

        fake_module = SimpleNamespace(UpdateManager=Manager)
        with mock.patch("updater.load_update_config", return_value={
            "version": "1.0.1", "source": "http", "url": feed
        }), mock.patch.dict(sys.modules, {"velopack": fake_module}):
            service = UpdateService(lambda _state: None)

        self.assertEqual(seen, [feed])
        self.assertEqual(service.state()["currentVersion"], "1.0.1")


if __name__ == "__main__":
    unittest.main()
