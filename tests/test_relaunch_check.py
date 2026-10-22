"""Relaunch evidence gate; no Coinbase credentials or network are used here."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.relaunch_check import verify_installation


SHA = "a" * 40


class FakeRunner:
    def __init__(self, *, dirty=False, tests_ok=True):
        self.dirty = dirty
        self.tests_ok = tests_ok
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(tuple(command))
        if command[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout=SHA + "\n", stderr="")
        if command[:2] == ["git", "status"]:
            return SimpleNamespace(returncode=0,
                                   stdout=" M src/advanced_live.py\n" if self.dirty else "",
                                   stderr="")
        if "unittest" in command:
            return SimpleNamespace(returncode=0 if self.tests_ok else 1,
                                   stdout="", stderr="FAILED (failures=1)")
        raise AssertionError("Unexpected subprocess invoked: " + repr(command))


class RelaunchGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reviewed_clean_checkout_tests_and_coinbase_preflight_pass(self):
        runner = FakeRunner()
        preflight_calls = []
        def inspect(*, require_history=False):
            preflight_calls.append(require_history)
            return {"execution_ready": True, "blockers": [], "warnings": []}
        result = verify_installation(expected_sha=SHA, root=self.root,
                                     runner=runner, inspector=inspect)
        self.assertTrue(result["release_check_passed"])
        self.assertTrue(result["revision_verified"])
        self.assertTrue(result["unit_tests_passed"])
        self.assertTrue(result["coinbase_preflight_passed"])
        self.assertEqual(preflight_calls, [True])
        self.assertFalse(result["placed_orders"])

    def test_wrong_git_revision_blocks_before_any_test_or_coinbase_call(self):
        runner = FakeRunner()
        def forbidden(**_kwargs):
            self.fail("No authenticated requests for mismatched code")
        result = verify_installation(expected_sha="b"*40, root=self.root,
                                     runner=runner, inspector=forbidden)
        self.assertFalse(result["release_check_passed"])
        self.assertIn("LOCAL_CODE_DIFFERS_FROM_REVIEWED_RELEASE",
                      result["blockers"])
        self.assertEqual(len(runner.calls), 1)

    def test_dirty_checkout_blocks_before_trade_account_query(self):
        runner = FakeRunner(dirty=True)
        result = verify_installation(
            expected_sha=SHA, root=self.root, runner=runner,
            inspector=lambda **_kwargs: self.fail("No authenticated requests when dirty"))
        self.assertFalse(result["release_check_passed"])
        self.assertIn("LOCAL_SOURCE_HAS_UNCOMMITTED_CHANGES",
                      result["blockers"])
        self.assertEqual(len(runner.calls), 2)

    def test_failed_local_regression_blocks_before_coinbase_query(self):
        runner = FakeRunner(tests_ok=False)
        result = verify_installation(
            expected_sha=SHA, root=self.root, runner=runner,
            inspector=lambda **_kwargs: self.fail("No account reads when tests fail"))
        self.assertFalse(result["release_check_passed"])
        self.assertIn("LOCAL_PYTHON_REGRESSION_TESTS_FAILED",
                      result["blockers"])
        self.assertFalse(result["coinbase_preflight_passed"])

    def test_unresolved_coinbase_order_cannot_pass_relaunch_gate(self):
        runner = FakeRunner()
        result = verify_installation(expected_sha=SHA, root=self.root,
            runner=runner, inspector=lambda **_kwargs: {
                "execution_ready": False,
                "blockers": ["UNRECONCILED_COINBASE_SUBMISSION"],
                "warnings": []})
        self.assertFalse(result["release_check_passed"])
        self.assertIn("AUTHENTICATED_READ_ONLY_PREFLIGHT_BLOCKED",
                      result["blockers"])
        self.assertIn("UNRECONCILED_COINBASE_SUBMISSION",
                      result["coinbase_preflight_blockers"])


if __name__ == "__main__":
    unittest.main()
