"""Unit tests for drytest stubbed overlay and pipeline harnesses in Dictator."""
import os
import sys
import unittest
from unittest.mock import MagicMock

for mod in ["keyboard", "sounddevice", "pystray", "faster_whisper", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk", "numpy"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import drytest


class TestDrytestStubs(unittest.TestCase):

    def test_stub_overlay_state_recording(self):
        overlay = drytest.StubOverlay()
        self.assertEqual(overlay.states, [])

        overlay.set_state("listening", "mic warm")
        overlay.set_state("transcribing")
        overlay.set_state("idle")

        self.assertEqual(overlay.states, ["listening", "transcribing", "idle"])

    def test_stub_overlay_set_level_safe(self):
        overlay = drytest.StubOverlay()
        # Ensure set_level does not throw on various inputs
        overlay.set_level(0.0)
        overlay.set_level(0.5)
        overlay.set_level(1.0)

    def test_make_app_isolation(self):
        app = drytest.make_app()
        self.assertIsInstance(app.overlay, drytest.StubOverlay)
        self.assertEqual(app.injected, [])
        self.assertEqual(app.recorded, [])

        # Verify simulated record dictation
        app._record_dictation("raw utterance", "cleaned utterance", 2.5, "fast")
        self.assertEqual(len(app.recorded), 1)
        self.assertEqual(app.recorded[0], ("raw utterance", "cleaned utterance", 2.5))


if __name__ == "__main__":
    unittest.main()
