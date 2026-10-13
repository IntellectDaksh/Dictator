"""Unit tests for tone resolution and folder size calculation in Dictator."""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

for mod in ["keyboard", "sounddevice", "pystray", "faster_whisper", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk", "numpy"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main


class TestToneAndStorage(unittest.TestCase):

    def test_tone_for_verbatim_apps(self):
        self.assertEqual(main.tone_for("code.exe", ""), "verbatim")
        self.assertEqual(main.tone_for("cmd.exe", ""), "verbatim")
        self.assertEqual(main.tone_for("windowsterminal.exe", ""), "verbatim")

    def test_tone_for_casual_apps(self):
        self.assertEqual(main.tone_for("slack.exe", ""), "casual")
        self.assertEqual(main.tone_for("discord.exe", ""), "casual")
        self.assertEqual(main.tone_for("whatsapp.exe", ""), "casual")

    def test_tone_for_formal_apps_and_titles(self):
        self.assertEqual(main.tone_for("winword.exe", ""), "formal")
        self.assertEqual(main.tone_for("outlook.exe", ""), "formal")
        # Test case-insensitivity and None handling
        self.assertEqual(main.tone_for("chrome.exe", "Gmail - Inbox"), "formal")
        self.assertIsNone(main.tone_for("notepad.exe", None))

    def test_tone_for_config_overrides(self):
        cfg = {
            "tone_overrides": {
                "casual": ["code.exe"],
                "formal": ["notepad.exe"]
            }
        }
        self.assertEqual(main.tone_for("code.exe", "", cfg), "casual")
        self.assertEqual(main.tone_for("notepad.exe", "", cfg), "formal")

    def test_folder_size_empty_and_populated(self):
        with tempfile.TemporaryDirectory() as td:
            size, count = main.folder_size(td)
            self.assertEqual(size, 0)
            self.assertEqual(count, 0)

            # Write temporary test file
            fpath = os.path.join(td, "test.txt")
            with open(fpath, "wb") as f:
                f.write(b"1234567890")

            size, count = main.folder_size(td)
            self.assertEqual(size, 10)
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
