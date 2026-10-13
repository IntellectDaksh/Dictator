"""Unit tests for configuration loading, saving, and defaults recovery in Dictator."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

for mod in ["keyboard", "sounddevice", "pystray", "faster_whisper", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk", "numpy"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main


class TestConfigManager(unittest.TestCase):

    def test_load_config_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            missing_path = os.path.join(td, "nonexistent_config.json")
            with patch("main.CONFIG_PATH", missing_path):
                cfg = main.load_config()
                self.assertEqual(cfg["enabled"], main.DEFAULTS["enabled"])
                self.assertEqual(cfg["cleanup_mode"], main.DEFAULTS["cleanup_mode"])
                self.assertEqual(cfg["model_size"], main.DEFAULTS["model_size"])

    def test_load_config_merges_custom_values(self):
        with tempfile.TemporaryDirectory() as td:
            test_path = os.path.join(td, "config.json")
            custom_data = {
                "cleanup_mode": "smart",
                "model_size": "medium.en",
                "beam_size": 2,
                "unsupported_key_xyz": "ignored",
            }
            with open(test_path, "w", encoding="utf-8") as f:
                json.dump(custom_data, f)

            with patch("main.CONFIG_PATH", test_path):
                cfg = main.load_config()
                self.assertEqual(cfg["cleanup_mode"], "smart")
                self.assertEqual(cfg["model_size"], "medium.en")
                self.assertEqual(cfg["beam_size"], 2)
                # Ensure unknown keys are filtered
                self.assertNotIn("unsupported_key_xyz", cfg)
                # Ensure missing keys retain defaults
                self.assertEqual(cfg["enabled"], main.DEFAULTS["enabled"])

    def test_load_config_recovers_from_corrupt_json(self):
        with tempfile.TemporaryDirectory() as td:
            corrupt_path = os.path.join(td, "corrupt.json")
            with open(corrupt_path, "w", encoding="utf-8") as f:
                f.write("{{{ broken json ...")

            with patch("main.CONFIG_PATH", corrupt_path):
                cfg = main.load_config()
                # Must not raise and should return defaults
                self.assertEqual(cfg["model_size"], main.DEFAULTS["model_size"])

    def test_save_config_creates_file_and_dir(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "DictatorAppData")
            config_path = os.path.join(app_dir, "config.json")

            with patch("main.APP_DIR", app_dir), patch("main.CONFIG_PATH", config_path):
                test_cfg = dict(main.DEFAULTS)
                test_cfg["cleanup_mode"] = "verbatim"
                main.save_config(test_cfg)

                self.assertTrue(os.path.isfile(config_path))
                with open(config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(saved["cleanup_mode"], "verbatim")


if __name__ == "__main__":
    unittest.main()
