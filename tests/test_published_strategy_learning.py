"""Offline tests for immutable freeze and truly future-only strategy learning."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.published_strategy_learning import PublishedRuleLearner
from src.published_strategy_research import BAR


def series(n=730, *, gap=None):
    return {
        i*BAR: {"ts": i*BAR, "open": 100+i*.2, "high": 100+i*.2+.3,
                "low": 100+i*.2-.3, "close": 100+i*.2, "volume": 100.}
        for i in range(n) if i != gap
    }


class ForwardRuleLearningTests(unittest.TestCase):
    def test_freeze_at_current_completed_candle_without_retroactive_profit(self):
        with tempfile.TemporaryDirectory() as folder:
            learner = PublishedRuleLearner(Path(folder)/"lessons.sqlite3")
            frozen = learner.observe("ETH-USD", series(720),
                frozen_rule=("donchian_20_10",),
                clock=lambda: 720*BAR+30)
            self.assertEqual(frozen["status"], "FROZEN")
            self.assertEqual(frozen["observed_forward_bars"], 0)
            self.assertNotIn("outcomes", frozen)
            self.assertEqual(learner.audit("ETH-USD")["events"], 0)

    def test_new_candles_score_only_after_frozen_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"lessons.sqlite3"
            learner = PublishedRuleLearner(path)
            learner.observe("ETH-USD", series(720),
                frozen_rule=("donchian_20_10",),
                clock=lambda: 720*BAR+30)
            next_run = PublishedRuleLearner(path)
            observed = next_run.observe("ETH-USD", series(730),
                clock=lambda: 730*BAR+30)
            self.assertEqual(observed["status"], "FORWARD_PAPER_OBSERVED")
            self.assertEqual(observed["observed_forward_bars"], 10)
            self.assertEqual(observed["first_forward_bar"], 720*BAR)
            self.assertEqual(observed["last_forward_bar"], 729*BAR)
            self.assertFalse(observed["promotion_authorized"])
            self.assertEqual(len(observed["outcomes"]), 7)
            self.assertEqual(next_run.audit("ETH-USD")["events"], 1)
            # Repeating the same historical snapshot cannot double-credit.
            again = next_run.observe("ETH-USD", series(730),
                clock=lambda: 730*BAR+30)
            self.assertEqual(again, observed)
            self.assertEqual(next_run.audit("ETH-USD")["events"], 1)

    def test_later_runs_keep_original_rule_and_append_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"ledger.sqlite3"
            gate = PublishedRuleLearner(path)
            gate.observe("BTC-USD", series(720), frozen_rule=("sma_band_10_30", .04),
                         clock=lambda: 720*BAR+60)
            gate.observe("BTC-USD", series(730), clock=lambda: 730*BAR+60)
            gate.observe("BTC-USD", series(745), clock=lambda: 745*BAR+60)
            self.assertEqual(gate.frozen("BTC-USD"), ("sma_band_10_30", .04))
            self.assertEqual(gate.audit("BTC-USD")["events"], 2)

    def test_changing_freeze_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            gate = PublishedRuleLearner(Path(folder)/"lessons.sqlite3")
            gate.observe("BTC-USD", series(720),
                         frozen_rule=("donchian_20_10",),
                         clock=lambda: 720*BAR+5)
            with self.assertRaisesRegex(ValueError, "FROZEN_RULE_MISMATCH"):
                gate.observe("BTC-USD", series(725),
                             frozen_rule=("donchian_55_20",),
                             clock=lambda: 725*BAR+5)

    def test_historical_backdating_is_not_forward_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            gate = PublishedRuleLearner(Path(folder)/"lessons.sqlite3")
            result = gate.observe("BTC-USD", series(720),
                                  clock=lambda: 750*BAR+5)
            self.assertEqual(result["status"], "DATA_UNAVAILABLE")
            self.assertIsNone(gate.frozen("BTC-USD"))

    def test_partial_history_remains_normal(self):
        with tempfile.TemporaryDirectory() as folder:
            gate = PublishedRuleLearner(Path(folder)/"lessons.sqlite3")
            self.assertEqual(
                gate.observe("BTC-USD", series(720, gap=80),
                             clock=lambda: 720*BAR+5)["status"],
                "PARTIAL")

    def test_ledger_tampering_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"ledger.sqlite3"
            gate = PublishedRuleLearner(path)
            gate.observe("BTC-USD", series(720),
                         clock=lambda: 720*BAR+5)
            gate.observe("BTC-USD", series(725),
                         clock=lambda: 725*BAR+5)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE observations SET evidence='{}'")
            with self.assertRaisesRegex(ValueError, "CORRUPT_FORWARD_RESEARCH"):
                gate.audit("BTC-USD")


if __name__ == "__main__":
    unittest.main()
