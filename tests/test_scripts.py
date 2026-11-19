"""Unit tests for Dictator helper scripts (validate_config, export_history, import_vocab, health_check)."""
import csv
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import validate_config
import export_history
import import_vocab
import health_check


class TestValidateConfig(unittest.TestCase):

    def test_missing_config(self):
        ok, msgs = validate_config.validate_config("nonexistent_dictator_cfg.json")
        self.assertFalse(ok)
        self.assertTrue(any("not found" in m for m in msgs))

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{invalid json")
            path = f.name
        try:
            ok, msgs = validate_config.validate_config(path)
            self.assertFalse(ok)
            self.assertTrue(any("Invalid JSON" in m for m in msgs))
        finally:
            os.unlink(path)

    def test_valid_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({
                "cleanup_mode": "fast",
                "model_size": "base.en",
                "beam_size": 2,
                "ollama_timeout_s": 10.0,
                "vocabulary": ["Dictator", "Whisper"]
            }, f)
            path = f.name
        try:
            ok, msgs = validate_config.validate_config(path)
            self.assertTrue(ok)
            self.assertEqual(msgs, [])
        finally:
            os.unlink(path)

    def test_invalid_bounds_and_types(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({
                "cleanup_mode": "unknown_mode",
                "beam_size": -1,
                "ollama_timeout_s": 0,
                "vocabulary": "not-a-list"
            }, f)
            path = f.name
        try:
            ok, msgs = validate_config.validate_config(path)
            self.assertFalse(ok)
            self.assertTrue(any("cleanup_mode" in m for m in msgs))
            self.assertTrue(any("beam_size" in m for m in msgs))
            self.assertTrue(any("ollama_timeout_s" in m for m in msgs))
            self.assertTrue(any("vocabulary" in m for m in msgs))
        finally:
            os.unlink(path)


class TestExportHistory(unittest.TestCase):

    def test_read_entries_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            in_file = os.path.join(td, "history.jsonl")
            csv_out = os.path.join(td, "out.csv")
            json_out = os.path.join(td, "out.json")

            sample_data = [
                {"timestamp": "2026-11-19T10:00:00", "duration_s": 3.5, "app": "notepad", "raw_transcript": "hello", "cleaned_text": "Hello."},
                {"timestamp": "2026-11-19T10:05:00", "duration_s": 2.0, "app": "code", "raw_transcript": "world", "cleaned_text": "World."}
            ]

            with open(in_file, "w", encoding="utf-8") as f:
                for row in sample_data:
                    f.write(json.dumps(row) + "\n")
                f.write("\n")  # empty line
                f.write("corrupt json line\n")

            entries = export_history.read_entries(in_file)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["cleaned_text"], "Hello.")

            export_history.export_csv(entries, csv_out)
            self.assertTrue(os.path.exists(csv_out))
            with open(csv_out, "r", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                self.assertEqual(len(reader), 2)
                self.assertEqual(reader[1]["app"], "code")

            export_history.export_json(entries, json_out)
            self.assertTrue(os.path.exists(json_out))
            with open(json_out, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                self.assertEqual(len(loaded), 2)


class TestImportVocab(unittest.TestCase):

    def test_read_and_import_vocabulary(self):
        with tempfile.TemporaryDirectory() as td:
            vocab_txt = os.path.join(td, "words.txt")
            cfg_path = os.path.join(td, "config.json")

            with open(vocab_txt, "w", encoding="utf-8") as f:
                f.write("# Comment line\n")
                f.write("Dictator, PyTorch\n")
                f.write("dictator\n")  # duplicate lower case
                f.write("Ollama\n")

            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"vocabulary": ["ExistingTerm", "pytorch"]}, f)

            words = import_vocab.read_vocabulary_file(vocab_txt)
            self.assertIn("Dictator", words)
            self.assertIn("PyTorch", words)
            self.assertIn("Ollama", words)

            ok = import_vocab.import_vocabulary(words, cfg_path)
            self.assertTrue(ok)

            with open(cfg_path, "r", encoding="utf-8") as f:
                res = json.load(f)
            # PyTorch should be skipped as duplicate of pytorch; Dictator and Ollama added
            vocab_lower = [w.lower() for w in res["vocabulary"]]
            self.assertIn("dictator", vocab_lower)
            self.assertIn("ollama", vocab_lower)
            self.assertIn("existingterm", vocab_lower)


class TestHealthCheck(unittest.TestCase):

    def test_check_python_version(self):
        ok, msg = health_check.check_python_version()
        self.assertTrue(ok)
        self.assertIn("Python 3.", msg)

    def test_check_storage(self):
        ok, msg = health_check.check_storage()
        self.assertTrue(ok)
        self.assertIn("Writable: True", msg)


if __name__ == "__main__":
    unittest.main()
