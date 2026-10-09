"""Unit tests for Word Error Rate (WER) and text normalization metrics in audit/bench.py."""
import os
import sys
import unittest
from unittest.mock import MagicMock

for mod in ["keyboard", "sounddevice", "pystray", "faster_whisper", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk", "numpy"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(ROOT, "audit")
for p in [ROOT, AUDIT]:
    if p not in sys.path:
        sys.path.insert(0, p)

import main
from bench import norm, wer


class TestBenchMetrics(unittest.TestCase):

    def test_norm_lowercases_and_replaces_punctuation(self):
        result = norm("Hello, World!")
        self.assertEqual(result, ["hello", "world"])

    def test_norm_expands_numbers_and_cpp(self):
        result = norm("whisper.cpp in 4k on v3")
        self.assertEqual(result, ["whisper", "dot", "cpp", "in", "four", "k", "on", "v", "three"])

    def test_wer_identical_strings(self):
        edits, ref_len = wer("the quick brown fox", "the quick brown fox")
        self.assertEqual(edits, 0)
        self.assertEqual(ref_len, 4)

    def test_wer_substitution(self):
        edits, ref_len = wer("the quick brown fox", "the fast brown fox")
        self.assertEqual(edits, 1)
        self.assertEqual(ref_len, 4)

    def test_wer_insertion_and_deletion(self):
        # insertion
        edits, ref_len = wer("hello world", "hello beautiful world")
        self.assertEqual(edits, 1)
        self.assertEqual(ref_len, 2)

        # deletion
        edits, ref_len = wer("hello beautiful world", "hello world")
        self.assertEqual(edits, 1)
        self.assertEqual(ref_len, 3)

    def test_wer_empty_hypothesis(self):
        edits, ref_len = wer("hello world", "")
        self.assertEqual(edits, 2)
        self.assertEqual(ref_len, 2)


if __name__ == "__main__":
    unittest.main()
