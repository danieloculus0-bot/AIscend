"""Regression coverage for BEAN's paired, durable, research-only capital lab."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.bean_capital_experiment import (
    MODES, REGIMES, digest, evaluate_path, experiment,
    train_q, verify_ledger,
)


class BeanCapitalExperimentTests(unittest.TestCase):
    def test_training_uses_real_q_updater_and_is_deterministic(self):
        q_a, receipts_a = train_q(base_seed=19, seed_count=2, periods=155)
        q_b, receipts_b = train_q(base_seed=19, seed_count=2, periods=155)
        self.assertEqual(digest(q_a), digest(q_b))
        self.assertEqual(receipts_a, receipts_b)
        self.assertTrue(q_a)
        self.assertTrue(any(
            row[1] != row[2] for row in receipts_a
        ), "Training must actually update Q values")

    def test_1000_bankroll_split_and_sqlite_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "trials.sqlite3"
            report_path = Path(folder) / "results.json"
            result = experiment(
                db_path=db_path, report_path=report_path,
                seed=14, train_seeds=2, holdout_seeds=2,
                periods=155, capital=1000)
            expected = 2 * len(REGIMES) * len(MODES)
            self.assertEqual(result["evaluation_episodes"], expected)
            self.assertEqual(result["holdout_market_episodes"], 8)
            self.assertEqual(result["ledger_verification"]["verified_episodes"],
                             expected)
            self.assertGreater(result["ledger_verification"]["verified_events"],
                               expected)
            self.assertFalse(result["adopt_for_live_trading"])
            self.assertIn("bean_holdout_total_orders", result)
            self.assertIn("bean_net_alpha_over_cash_mean_usd", result)
            self.assertEqual(result["trading_skill_demonstrated"],
                             result["bean_holdout_total_orders"] > 0
                             and result["scoreboard"]["bean_frozen"]["mean_end_usd"] > 1000
                             and result["scoreboard"]["bean_frozen"]["mean_end_usd"]
                                 > result["scoreboard"]["price_action"]["mean_end_usd"])
            self.assertEqual(result["exchange_orders"], 0)
            self.assertFalse(result["historical_coinbase_prices_used"])
            self.assertEqual(result["initial_bankroll_usd_per_run"], 1000)
            self.assertTrue(report_path.exists())
            with sqlite3.connect(db_path) as db:
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM training").fetchone()[0], 8)
                pairs = db.execute(
                    "SELECT seed,regime,COUNT(DISTINCT policy) "
                    "FROM evaluations GROUP BY seed,regime").fetchall()
                self.assertEqual(len(pairs), 8)
                self.assertTrue(all(n == len(MODES) for _, _, n in pairs))
                self.assertTrue(all(row[0] >= 1000014 for row in pairs))
            with self.assertRaises(FileExistsError):
                experiment(db_path=db_path, report_path=report_path,
                           seed=14, train_seeds=1, holdout_seeds=1,
                           periods=155)
            self.assertEqual(json.loads(report_path.read_text())["q_sha256"],
                             result["q_sha256"])

    def test_ledger_tampering_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "evidence.sqlite3"
            report_path = Path(folder) / "evidence.json"
            experiment(db_path=db_path, report_path=report_path,
                       train_seeds=1, holdout_seeds=1, periods=151)
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "UPDATE events SET payload='{}' WHERE rowid="
                    "(SELECT MIN(rowid) FROM events)")
            with self.assertRaisesRegex(AssertionError, "SHA-256"):
                verify_ledger(db_path)

    def test_evaluator_cannot_see_future_prices_when_choosing(self):
        """The first decision's record is unchanged by future price mutations."""
        with tempfile.TemporaryDirectory() as folder:
            # Create matching input ledgers without using any exchange provider.
            for j, tail in enumerate(([1] * 169, [3] * 169)):
                db_path = Path(folder) / f"eval{j}.sqlite3"
                with sqlite3.connect(db_path) as db:
                    from scripts.bean_capital_experiment import prepare
                    prepare(db)
                    prices = [1.0, 1.01, *tail]
                    evaluate_path(
                        db, seed=1, regime="bull", mode="bean_frozen",
                        prices=prices, q={}, capital=1000,
                        fee_bps=60, spread_bps=10, slippage_bps=5)
                    db.commit()
                with sqlite3.connect(db_path) as db:
                    decision = db.execute(
                        "SELECT payload FROM events WHERE type='DECISION' "
                        "ORDER BY seq LIMIT 1").fetchone()[0]
                if j == 0:
                    first = decision
                else:
                    self.assertEqual(first, decision)


if __name__ == "__main__":
    unittest.main()
