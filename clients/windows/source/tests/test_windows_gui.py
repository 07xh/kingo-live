"""Headless checks for Windows launcher/configuration paths. No GUI and no network."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from windows_gui import Application, app_dir, bundled_config, ensure_config, read_config


class WindowsGuiConfigTest(unittest.TestCase):
    def test_bundled_template_is_readable_offline(self):
        path = bundled_config()
        self.assertTrue(path.is_file())
        config = read_config(path, offline=True)
        self.assertEqual(config["strategy"]["dry_run"], True)
        self.assertFalse(config["protocol"]["verified"])

    def test_config_created_without_overwriting_existing(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("windows_gui.app_dir", return_value=Path(td)):
                first = ensure_config()
                self.assertTrue(first.is_file())
                first.write_text("custom value", encoding="utf-8")
                self.assertEqual(ensure_config().read_text(encoding="utf-8"), "custom value")

    def test_history_panel_uses_offline_inspector_without_opening_school(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            expected = Path(td) / "readonly.sqlite3"
            with patch.dict(os.environ, {"KINGO_INTENT_DB": str(expected)}), \
                 patch("windows_gui.open_history_window") as inspect, \
                 patch("windows_gui.webbrowser.open",
                       side_effect=AssertionError("network UI not allowed")):
                Application.open_history_inspector(SimpleNamespace())
            self.assertEqual(inspect.call_args.kwargs["default_sqlite"], expected)

    def test_default_real_monitor_is_forbidden(self):
        with self.assertRaises(ValueError):
            read_config(bundled_config(), offline=False)


if __name__ == "__main__":
    unittest.main()
