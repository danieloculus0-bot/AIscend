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

    def test_query_uses_pair_subsets_and_translation_layer(self):
        for i in range(10):
            self.add(i)
        report=self.ledger.prior(
            product="BTC-USD",state=self.state,as_of="2020-01-20")
        self.assertEqual(report["retrieval_strategy"],
                         "sqlite_four_pair_inverted_index")
        self.assertEqual(report["candidate_rows_returned"],10)
        self.assertEqual(report["reference_codes"],{"02":10})
        self.assertEqual(report["flagged_subsets"][0]["field"],"momentum_7d")
        self.assertEqual(report["flagged_subsets"][0]["meaning"],"up")
        self.assertEqual(len(report["flagged_subsets"]),4)
        self.assertEqual(self.ledger.validate()["indexed_subset_pointers"],40)
        # Confirm SQLite's query planner uses the composite inverted index.
        plan=self.ledger.db.execute(
            "EXPLAIN QUERY PLAN SELECT memory_seq FROM memory_flags "
            "WHERE product=? AND slot=? AND pair=? AND exit_day<?",
            ("BTC-USD",1,"ac","2020-01-20")).fetchall()
        self.assertTrue(any("flag_lookup" in row["detail"] for row in plan))

    def test_index_lookup_equals_original_full_history_scan(self):
        states=[
            self.state,
            {**self.state,"momentum_7d":"flat"},
            {**self.state,"momentum_7d":"down","volatility_14d":"high"},
        ]
        # Comparable experiences have overlapping variable pairs.
        for i in range(10):
            state=states[i%3]
            self.ledger.add(
                episode="var:"+str(i),product="BTC-USD",state=state,
                action="trade",decision_day="2020-01-01",
                exit_day=f"2020-01-{i+2:02d}",net_return=(i-3)/100,
                gross_return=(i-3)/100+.013,evidence_sha="a"*64)
        from src.bean_pair_index import encode_prefix
        from scripts.bean_five_year_memory_trial import (
            MAX_CANDIDATES, SIMILARITY_FLOOR)
        for query in states:
            for cutoff in ("2020-01-05","2020-01-20"):
                index=encode_prefix("BTC-USD",query)
                legacy=[]
                for r in self.ledger.db.execute(
                        "SELECT * FROM memories WHERE product=? AND exit_day<?",
                        ("BTC-USD",cutoff)):
                    matches=sum(
                        index[off:off+2]==r["prefix"][off:off+2]
                        for off in (2,4,6,8))
                    if matches>=SIMILARITY_FLOOR:
                        legacy.append((matches,r))
                legacy.sort(key=lambda x:(-x[0],-x[1]["seq"]))
                legacy=legacy[:MAX_CANDIDATES]
                expected={r["diagnostic"] for _,r in legacy}
                report=self.ledger.prior(
                    product="BTC-USD",state=query,as_of=cutoff)
                self.assertEqual(report["count"],len(legacy))
                self.assertEqual(set(report["reference_codes"]),expected)
                self.assertEqual(report["latest_matched_exit_day"],
                                 max((r["exit_day"] for _,r in legacy),
                                     default=None))

    def test_corrupted_subset_pointer_is_rejected(self):
        self.add(1)
        self.ledger.db.execute(
            "UPDATE memory_flags SET pair='aa' "
            "WHERE memory_seq=1 AND slot=1")
        with self.assertRaisesRegex(ValueError,"feature pointer"):
            self.ledger.validate()

    def test_bounded_retrieval_never_crosses_products(self):
        for i in range(10):
            self.add(i)
        eth=self.ledger.prior(product="ETH-USD",state=self.state,
                              as_of="2020-01-20")
        self.assertEqual(eth["count"],0)


if __name__=="__main__":
    unittest.main()
