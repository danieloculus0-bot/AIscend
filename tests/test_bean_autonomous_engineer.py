from __future__ import annotations

import json
import os
from io import BytesIO
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.bean_autonomous_engineer import (
    Engineer, Lessons, OllamaAuthor, OLLAMA_GENERATION_TIMEOUT,
    clean_environment, checked_ollama_url, validate_plan,
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

    def test_local_ollama_chat_has_extended_timeout_and_bounded_context(self):
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, _limit):
                return self.payload

        def fake_open(request, timeout):
            payload = json.loads(request.data) if request.data else {}
            calls.append((request.full_url, timeout, payload))
            result = {
                "message": {"content": json.dumps({
                    "edits": [{"find": "return 1", "replace": "return 2"}],
                    "hypothesis": "fix",
                })}
            }
            return FakeResponse(json.dumps(result).encode())

        author = OllamaAuthor(model="qwen2.5-coder:1.5b", opener=fake_open)
        result = author.patch("src/example.py", "def f():\\n    return 1\\n", [])
        self.assertIn("return 2", result["code"])
        _, timeout, payload = calls[0]
        self.assertEqual(timeout, OLLAMA_GENERATION_TIMEOUT)
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(payload["options"]["num_predict"], 1100)

    def test_ollama_timeout_returns_actionable_failure(self):
        def fake_open(_request, _timeout):
            raise TimeoutError("timed out")

        author = OllamaAuthor(model="qwen2.5-coder:1.5b", opener=fake_open)
        with self.assertRaisesRegex(RuntimeError, "Ollama /api/chat timed out"):
            author.patch("src/example.py", "def f():\\n    return 1\\n", [])

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

    def test_unattended_successful_patch_is_merged_without_user_prompt(self):
        class Author:
            def patch(self, target, source, lessons):
                return {"code": source.replace("return 1", "return 2"),
                        "hypothesis": "Repair score result"}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            checkout = root / "aiscend"
            checkout.mkdir()
            (checkout / "src").mkdir()
            (checkout / "tests").mkdir()
            (checkout / "src" / "bean_strategy_lab.py").write_text(
                "def score():\n    return 1\n", encoding="utf-8")
            (checkout / "tests" / "test_smoke.py").write_text(
                "import unittest\nfrom src.bean_strategy_lab import score\n"
                "class Smoke(unittest.TestCase):\n"
                "    def test_scoring(self):\n        self.assertEqual(score(), 2)\n",
                encoding="utf-8")
            def cmd(*args):
                return subprocess.run(
                    list(args), cwd=checkout, check=True,
                    capture_output=True, text=True,
                )
            cmd("git", "init", "-b", "main")
            cmd("git", "add", ".")
            cmd("git", "-c", "user.name=Local Test",
                "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
            engineer = Engineer(checkout, author=Author(),
                                lessons=Lessons(root / "lessons.jsonl"),
                                python=sys.executable)
            result = engineer.iteration()
            self.assertEqual(result["state"], "ADOPTED_AUTOMATICALLY", result)
            self.assertEqual(
                (checkout / "src" / "bean_strategy_lab.py").read_text(encoding="utf-8"),
                "def score():\n    return 2\n")
            self.assertIn("repair", result["hypothesis"].lower())
            self.assertEqual(len(engineer.lessons.history()), 1)

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
