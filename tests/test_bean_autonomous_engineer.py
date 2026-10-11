from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.bean_autonomous_engineer import (
    Engineer, Lessons, clean_environment, checked_ollama_url, validate_plan,
)


class AutonomousEngineerTests(unittest.TestCase):
    def test_structured_code_patch_is_applied_without_review(self):
        source = "def score(x):\n    return x - 1\n"
        plan = {"edits": [{"find": "return x - 1", "replace": "return x + 1"}],
                "hypothesis": "Correct scoring direction"}
        result = validate_plan(plan, source)
        self.assertEqual(result["code"], "def score(x):\n    return x + 1\n")
        self.assertIn("scoring", result["hypothesis"])

    def test_refuses_invalid_or_ambiguous_edits(self):
        source = "def score(x):\n    return x\n\ndef next_score(x):\n    return x\n"
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            validate_plan({"edits": [{"find": "return x", "replace": "return x+2"}]},
                          source)
        with self.assertRaises(SyntaxError):
            validate_plan({"edits": [{"find": "return x", "replace": "return ("}]},
                          "def f(x):\n    return x\n")
        with self.assertRaises(ValueError):
            validate_plan({"edits": []}, source)

    def test_local_ollama_transport_only(self):
        self.assertEqual(checked_ollama_url("http://127.0.0.1:11434"),
                         "http://127.0.0.1:11434")
        for url in ("https://example.com:11434", "http://10.0.0.1:11434",
                    "http://localhost:11434@attacker.tld:8000",
                    "http://localhost:11434/path"):
            with self.assertRaises(ValueError):
                checked_ollama_url(url)

    def test_wallet_env_is_not_passed_to_candidate_tests(self):
        with patch.dict(os.environ, {"CDP_API_KEY_SECRET": "private",
                                     "OPENROUTER_API_KEY": "secret",
                                     "SAFE_BUILD_FLAG": "1"}):
            env = clean_environment()
        self.assertNotIn("CDP_API_KEY_SECRET", env)
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertEqual(env["SAFE_BUILD_FLAG"], "1")

    def test_persistent_receipts_drive_next_iteration(self):
        with tempfile.TemporaryDirectory() as td:
            lessons = Lessons(Path(td) / "lessons.jsonl")
            lessons.record({"state": "FAILED", "target": "src/bean_strategy_lab.py",
                            "error": "bad patch"})
            lessons.record({"state": "ADOPTED_AUTOMATICALLY",
                            "target": "src/advanced_live.py"})
            restored = Lessons(Path(td) / "lessons.jsonl").history()
            self.assertEqual([x["state"] for x in restored],
                             ["FAILED", "ADOPTED_AUTOMATICALLY"])
            self.assertIn("ts", restored[0])

    def test_uncommitted_checkout_is_left_unchanged(self):
        class DummyAuthor:
            def patch(self, *args):
                self.fail("not expected")
        with tempfile.TemporaryDirectory() as td:
            runner = Engineer(Path(td), author=DummyAuthor(),
                              lessons=Lessons(Path(td) / "events.jsonl"))
            runner.git = lambda *args, **kwargs: (
                "main" if args == ("branch", "--show-current") else
                " M src/advanced_live.py" if args == ("status", "--porcelain") else ""
            )
            with self.assertRaisesRegex(RuntimeError, "uncommitted changes"):
                runner.iteration()


if __name__ == "__main__":
    unittest.main()
