"""Honest paper evidence import boundaries; real SpongeTD checked by CI."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.bean_core_paper_learning import paper_receipts, learn_from_paper
from src.bean_five_minute import BAR
from src.forward_paper import _db, freeze


class PaperCoreLearningContracts(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.paper = Path(self.folder.name) / "paper.sqlite3"
        self.learn = Path(self.folder.name) / "sponge.sqlite3"
        self.product = "BTC-USD"
        self.policy = "trend"
        self.signal = 2000 * BAR
        self.observed = (2001 * BAR) + 1
        self.entry = self.signal + 2 * BAR
        self.exit = self.entry + 72 * BAR
        with closing(sqlite3.connect(self.paper)) as db:
            _db(db)
            sha = freeze(db, product=self.product, policy=self.policy,
                         now=self.observed, fee_bps=60, spread_bps=10)
            entry, exit_price = 100.0, 101.0
            earned = exit_price / entry * (1 - .0065) ** 2 - 1
            db.execute(
                "INSERT INTO paper_decisions(product,policy_sha,signal_ts,"
                "observed_at,entry_ts,exit_ts,state,entry_price,exit_price,"
                "net_return) VALUES(?,?,?,?,?,?,'SCORED',?,?,?)",
                (self.product, sha, self.signal, self.observed, self.entry,
                 self.exit, entry, exit_price, earned))
            db.commit()

    def test_honest_causal_fee_net_receipt(self):
        rows = paper_receipts(self.paper)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_ts"], self.entry)
        self.assertGreater(rows[0]["entry_ts"], self.observed)
        # A 1% gross increase still loses money after the modeled
        # 1.3% round-trip taker+spread cost. Never train BEAN on gross P/L.
        self.assertLess(rows[0]["net_return"], 0)
        self.assertAlmostEqual(rows[0]["net_return"], -0.0030873275, places=8)
        self.assertNotIn("price", rows[0])

    def test_prior_bar_entry_or_mutated_pnl_never_teaches_bean(self):
        with closing(sqlite3.connect(self.paper)) as db:
            db.execute("UPDATE paper_decisions SET entry_ts=?",
                       (self.signal + BAR,))
            db.commit()
        with self.assertRaisesRegex(ValueError, "causal timestamps"):
            paper_receipts(self.paper)

    def test_fee_overstated_return_is_rejected(self):
        with closing(sqlite3.connect(self.paper)) as db:
            db.execute("UPDATE paper_decisions SET net_return=5.0")
            db.commit()
        with self.assertRaisesRegex(ValueError, "execution costs"):
            paper_receipts(self.paper)

    def test_missing_actual_core_is_error_not_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(ValueError):
                learn_from_paper(core_dir=empty, paper_db=self.paper,
                                 learning_db=self.learn)

    def test_core_paper_receipts_are_only_sent_to_credential_free_worker(self):
        class Completed:
            stdout = '{"q_updates":0,"new_feedback_updates":0}'
        with patch("src.bean_core_paper_learning.core_identity",
                   return_value={"sha": "f"*40, "source": "temp-core"}), patch(
                       "src.bean_core_paper_learning.subprocess.run",
                       return_value=Completed()) as worker:
            result = learn_from_paper(core_dir=self.folder.name,
                                      paper_db=self.paper, learning_db=self.learn)
        self.assertFalse(result["paper_only"] is False)
        self.assertEqual(result["execution_authority"], "none")
        args, kwargs = worker.call_args
        self.assertTrue(args[0][0].endswith("python") or
                        "python" in args[0][0].lower())
        self.assertIn("records", kwargs["input"])
        self.assertNotIn("api_key", kwargs["input"].lower())
        self.assertNotIn("ADVANCED_TRADE_API_KEY", kwargs["env"])
        # Windows runners may use equivalent long and 8.3 short temp paths.
        # Verify the actual checkout directory, not its textual spelling.
        self.assertTrue(Path(kwargs["env"]["PYTHONPATH"]).samefile(self.folder.name))


if __name__ == "__main__":
    unittest.main()
