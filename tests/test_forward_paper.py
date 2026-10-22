"""No real orders; test causal forward paper decisions with deterministic bars."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.bean_five_minute import BAR
from src.forward_paper import observe_and_score

MANIFEST=[{"product":"BTC-USD","policy":"trend",
           "training_only_frozen":True}]


class ForwardPaperTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/"paper.sqlite3"
        self.times=[]
        self.rows={}
        for index in range(900):
            ts=index*BAR
            price=100*(1.0006**index)
            self.rows[ts]={"open":price,"close":price*1.0002,"volume":100}
            self.times.append(ts)
        self.requests=[]
        self.missing=set()
    def fetch(self,product,start,end):
        self.assertEqual(product,"BTC-USD")
        self.requests.append((start,end))
        return {t:v for t,v in self.rows.items()
                if start<=t<end and t not in self.missing}
    def simulate_tick(self,at,**kwargs):
        return observe_and_score(
            manifest=MANIFEST,db_path=self.path,fetcher=self.fetch,
            clock=lambda:at*BAR+1,**kwargs)

    def test_signal_is_recorded_before_next_entry_bar_exists(self):
        result=self.simulate_tick(440)
        self.assertTrue(result["paper_only"])
        self.assertFalse(result["promotion_authorized"])
        signal=result["products"]["BTC-USD"]
        self.assertEqual(signal["status"],"PENDING")
        self.assertEqual(signal["signal_ts"],439*BAR)
        self.assertEqual(signal["exit_ts"],512*BAR)
        self.assertTrue(all(end<=440*BAR for _,end in self.requests))
        self.assertEqual(result["scorecard"]["BTC-USD"]["scored_trades"],0)
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute(
                "SELECT state,entry_price,net_return FROM paper_decisions").fetchone()
        self.assertEqual(row[0],"PENDING")
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])

    def test_history_is_scored_only_after_exit_candle_completed(self):
        self.simulate_tick(440)
        before=self.simulate_tick(500)
        self.assertEqual(before["products"]["BTC-USD"]["status"],"PENDING")
        done=self.simulate_tick(514)
        self.assertEqual(done["products"]["BTC-USD"]["status"],"SCORED")
        self.assertEqual(done["scorecard"]["BTC-USD"]["scored_trades"],1)
        self.assertGreater(done["scorecard"]["BTC-USD"]["net_return"],0)
        self.assertFalse(done["scorecard"]["BTC-USD"]["candidate_validated"])
        self.assertFalse(done["promotion_authorized"])

    def test_gap_over_open_paper_position_is_not_profitable_trade(self):
        self.simulate_tick(440)
        self.missing.add(482*BAR)
        done=self.simulate_tick(514)
        self.assertEqual(done["products"]["BTC-USD"]["status"],"DATA_GAP")
        self.assertEqual(done["scorecard"]["BTC-USD"]["net_return"],0)
        self.assertEqual(done["scorecard"]["BTC-USD"]["scored_trades"],0)

    def test_repeated_observation_in_same_slot_cannot_duplicate_signal(self):
        self.simulate_tick(440)
        self.simulate_tick(440)
        with closing(sqlite3.connect(self.path)) as db:
            n=db.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0]
        self.assertEqual(n,1)

    def test_frozen_policy_cannot_change_or_be_retroactively_tuned(self):
        self.simulate_tick(440)
        with self.assertRaisesRegex(ValueError,"Frozen strategy"):
            observe_and_score(
                manifest=[{"product":"BTC-USD","policy":"breakout",
                           "training_only_frozen":True}],
                db_path=self.path,fetcher=self.fetch,
                clock=lambda:441*BAR+1)
        with self.assertRaisesRegex(ValueError,"cost assumptions"):
            self.simulate_tick(441,fee_bps=25)
        with self.assertRaises(ValueError):
            observe_and_score(
                manifest=[{"product":"BTC-USD","policy":"trend",
                           "training_only_frozen":False}],
                db_path=self.path,fetcher=self.fetch,
                clock=lambda:441*BAR+1)

    def test_no_signals_on_incomplete_initial_lookback(self):
        short=lambda p,s,e:{t:v for t,v in self.rows.items()
                            if 430*BAR<=t<e}
        r=observe_and_score(manifest=MANIFEST,db_path=self.path,
                            fetcher=short,clock=lambda:440*BAR+1)
        self.assertEqual(r["products"]["BTC-USD"]["status"],"NO_SIGNAL")


if __name__=="__main__":
    unittest.main()
