import time
import unittest

from updater import UpdateService


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


if __name__ == "__main__":
    unittest.main()
