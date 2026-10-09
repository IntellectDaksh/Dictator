"""Unit tests for Dictator text cleanup and command transformations."""
import os
import sys
import unittest
from unittest.mock import MagicMock

# Mock hardware/GUI dependencies so unit tests run in any Python environment
for mod in ["keyboard", "sounddevice", "pystray", "faster_whisper", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk", "numpy"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main


class TestTextCleanup(unittest.TestCase):

    def test_fmt_bytes_zero_and_small(self):
        self.assertEqual(main.fmt_bytes(0), "0 B")
        self.assertEqual(main.fmt_bytes(512), "512 B")
        self.assertEqual(main.fmt_bytes(1023), "1023 B")

    def test_fmt_bytes_scales(self):
        self.assertEqual(main.fmt_bytes(1024), "1.0 KB")
        self.assertEqual(main.fmt_bytes(1024 * 1024), "1.0 MB")
        self.assertEqual(main.fmt_bytes(1024 * 1024 * 1024 * 2.5), "2.5 GB")

    def test_remove_filler_words(self):
        cases = [
            ("um hello", "hello"),
            ("hello uh world", "hello world"),
            ("i mean this is kind of cool", "this is cool"),
            ("", ""),
            (None, None),
        ]
        for inp, expected in cases:
            got = main.remove_filler_words(inp)
            self.assertEqual(got.strip() if got else got, expected)

    def test_apply_commands_formatting(self):
        cases = [
            ("first sentence new line second sentence", "first sentence\nsecond sentence"),
            ("intro new paragraph body", "intro\n\nbody"),
            ("item bullet point point one", "item\n- point one"),
        ]
        for inp, expected in cases:
            self.assertEqual(main.apply_commands(inp), expected)

    def test_apply_voice_command_tones(self):
        cleaned, tone = main.apply_voice_command("hello world make it formal")
        self.assertEqual(cleaned, "hello world")
        self.assertEqual(tone, "formal")

        cleaned2, tone2 = main.apply_voice_command("just a regular sentence")
        self.assertEqual(cleaned2, "just a regular sentence")
        self.assertIsNone(tone2)


if __name__ == "__main__":
    unittest.main()
