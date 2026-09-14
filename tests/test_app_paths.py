import os
import tempfile
import unittest
from unittest import mock

import app_paths


class AppPathMigrationTests(unittest.TestCase):
    def test_example_config_is_not_promoted_to_user_config(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as destination:
            with open(os.path.join(source, "dataverse_config.example.json"), "w", encoding="utf-8") as handle:
                handle.write("{}")
            with mock.patch.object(app_paths, "install_dir", return_value=source), \
                    mock.patch.object(app_paths.sys, "_MEIPASS", source, create=True):
                app_paths._migrate_legacy_data(destination)
            self.assertFalse(os.path.exists(os.path.join(destination, "dataverse_config.json")))

    def test_bundled_real_config_is_migrated_once(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as destination:
            bundled = os.path.join(source, "dataverse_config.json")
            with open(bundled, "w", encoding="utf-8") as handle:
                handle.write('{"environment_url":"https://example.invalid","tenant_id":"tenant","client_id":"client"}')
            with mock.patch.object(app_paths, "install_dir", return_value=source), \
                    mock.patch.object(app_paths.sys, "_MEIPASS", source, create=True):
                app_paths._migrate_legacy_data(destination)
            migrated = os.path.join(destination, "dataverse_config.json")
            self.assertTrue(os.path.exists(migrated))
            with open(migrated, "r", encoding="utf-8") as handle:
                self.assertIn("example.invalid", handle.read())


if __name__ == "__main__":
    unittest.main()
