"""Regression gates: BEAN memory is strictly past-only, hashed, and typed."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.bean_five_year_memory_trial import RetentionLedger


class MemoryBlindTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.ledger=RetentionLedger(Path(self.folder.name)/"test.sqlite3")
        self.addCleanup(self.ledger.close)
        self.state={
            "momentum_7d":"up", "momentum_28d":"flat",
            "momentum_84d":"down", "volatility_14d":"normal",
        }

    def add(self, i, *, result=.04):
        return self.ledger.add(
            episode="BTC-USD:"+str(i),product="BTC-USD",
            state=self.state, action="trade",
            decision_day="2020-01-01",exit_day=f"2020-01-{i+2:02d}",
            net_return=result,gross_return=result+.013,
            evidence_sha="a"*64)

    def test_no_future_knowledge_even_when_sqlite_contains_it(self):
        for i in range(10):
            self.add(i)
        early=self.ledger.prior(
            product="BTC-USD", state=self.state, as_of="2020-01-02")
        self.assertEqual(early["count"],0)
        middle=self.ledger.prior(
            product="BTC-USD",state=self.state,as_of="2020-01-08")
        self.assertEqual(middle["count"],6)
        self.assertEqual(middle["shrunk_trade_edge_bps"],0)
        later=self.ledger.prior(
            product="BTC-USD",state=self.state,as_of="2020-01-20")
        self.assertEqual(later["count"],10)
        self.assertGreater(later["shrunk_trade_edge_bps"],0)
        self.assertLess(later["latest_matched_exit_day"],later["as_of"])
        self.assertEqual(self.ledger.validate()["rows"],10)

    def test_root_cause_codes_and_exact_ascii_16_byte_index(self):
        self.add(1,result=-.03)
        row=self.ledger.db.execute("SELECT * FROM memories").fetchone()
        self.assertEqual(len(row["code"].encode("ascii")),16)
        self.assertEqual(row["diagnostic"],"01")
        self.assertEqual(row["prefix"],row["code"][:10])
        with self.assertRaises(sqlite3.IntegrityError):
            self.add(1,result=.05)
        self.ledger.db.execute(
            "UPDATE memories SET net_bps=99999 WHERE seq=1")
        with self.assertRaises(ValueError):
            self.ledger.validate()

    def test_bounded_retrieval_never_crosses_products(self):
        for i in range(10):
            self.add(i)
        eth=self.ledger.prior(product="ETH-USD",state=self.state,
                              as_of="2020-01-20")
        self.assertEqual(eth["count"],0)


if __name__=="__main__":
    unittest.main()
