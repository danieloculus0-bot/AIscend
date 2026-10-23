"""BEAN prospective research tests: no wallet, future fills or fake forward P/L."""
from __future__ import annotations
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.bean_sixhour_forward import inspect
from src.bean_six_hour_universe import BAR


def candles(count=200, change=.001):
    result = {}
    close = 100.
    for i in range(count):
        op = close
        close *= 1+change
        result[i*BAR] = {"ts":i*BAR,"open":op,"close":close,"volume":10.}
    return result


class BeanProspectiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"frozen.json"
        self.db = Path(self.temp.name)/"bean.sqlite3"
        self.manifest = {
            "schema":"aiscend.bean.sixhour.prospective.freeze.v1",
            "promotion_authorized":False,
            "frozen_at_unix":50*BAR+600,
            "fee_bps_per_side":60,
            "spread_bps_roundtrip":10,
            "min_forward_completed_bars":120,
            "min_realized_roundtrips":5,
            "min_excess_return_vs_cash":.02,
            "min_excess_return_vs_buy_hold":.02,
            "max_forward_drawdown":.20,
            "candidates":[{"product":"BTC-USD","policy":"bean_q",
                           "q_seed":{"up/calm":{"long":100.,"cash":0.}}}],
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.manifest),encoding="utf-8")

    def run_step(self, n, source=None):
        history = candles() if source is None else source
        return inspect(self.path,self.db,clock=lambda:n*BAR+600,
                       history_fetcher=lambda _product,**_kw:history)

    def test_prospective_entry_is_never_filled_at_open_before_signal(self):
        first = self.run_step(60)["markets"]["BTC-USD"]
        self.assertEqual(first["orders"],0)
        self.assertEqual(first["forward_bars"],0)
        self.assertEqual(first["pending"]["entry_ts"],61*BAR)
        self.assertLess(first["pending"]["selected_at"],
                        first["pending"]["entry_ts"])
        # Six-hour candle starting at 61 is NOT complete yet at t=61h.
        interim = self.run_step(61)["markets"]["BTC-USD"]
        self.assertEqual(interim["orders"],0)
        confirmed = self.run_step(62)["markets"]["BTC-USD"]
        self.assertEqual(confirmed["orders"],1)
        self.assertEqual(confirmed["q_updates"],1)
        self.assertGreaterEqual(confirmed["forward_bars"],2)
        self.assertFalse(confirmed["promotion_authorized"])

    def test_candle_opened_before_freeze_never_counts_as_forward(self):
        # Freeze happens inside candle 60. Its later close is still historical.
        self.manifest["frozen_at_unix"] = 60 * BAR + 600
        self.save()
        initial = self.run_step(60)["markets"]["BTC-USD"]
        self.assertEqual(initial["forward_bars"], 0)
        self.assertEqual(initial["pending"]["entry_ts"], 61 * BAR)

        excluded = self.run_step(61)["markets"]["BTC-USD"]
        self.assertEqual(excluded["forward_bars"], 0)
        self.assertEqual(excluded["orders"], 0)
        self.assertEqual(excluded["q_updates"], 0)
        self.assertEqual(excluded["fee_net_paper_return"], 0.)
        self.assertEqual(excluded["pending"]["entry_ts"], 61 * BAR)
        with closing(sqlite3.connect(self.db)) as db:
            events = [json.loads(row[0]) for row in
                      db.execute("SELECT data FROM events ORDER BY seq")]
        skipped = [e for e in events
                   if e["type"] == "pre_freeze_candle_excluded"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["candle_start"], 60 * BAR)
        self.assertFalse(any(e["type"] == "completed_candle_mark"
                             and e["candle_start"] == 60 * BAR for e in events))

        eligible = self.run_step(62)["markets"]["BTC-USD"]
        self.assertEqual(eligible["forward_bars"], 1)
        self.assertEqual(eligible["orders"], 1)
        self.assertEqual(eligible["q_updates"], 1)
        self.assertFalse(eligible["promotion_authorized"])

    def test_late_initialization_does_not_backfill_pre_freeze_bar(self):
        self.manifest["frozen_at_unix"] = 60 * BAR + 600
        self.save()
        initial = self.run_step(61)["markets"]["BTC-USD"]
        self.assertEqual(initial["forward_bars"], 0)
        self.assertEqual(initial["orders"], 0)
        self.assertEqual(initial["pending"]["entry_ts"], 62 * BAR)
        next_observation = self.run_step(62)["markets"]["BTC-USD"]
        self.assertEqual(next_observation["forward_bars"], 1)
        self.assertEqual(next_observation["orders"], 0)

    def test_identical_observation_is_idempotent(self):
        self.run_step(60)
        once=self.run_step(62)
        twice=self.run_step(62)
        self.assertEqual(once["ledger_tip"],twice["ledger_tip"])
        self.assertEqual(once["markets"],twice["markets"])

    def test_freeze_manifest_cannot_be_replaced_after_start(self):
        self.run_step(60)
        self.manifest["fee_bps_per_side"]=0
        self.save()
        with self.assertRaises(ValueError):
            self.run_step(62)

    def test_sqlite_state_edit_fails_hash_integrity(self):
        self.run_step(60)
        # sqlite3's transaction context does not close its handle on Windows.
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE positions SET state='{}' WHERE product='BTC-USD'")
        with self.assertRaises(ValueError):
            self.run_step(62)

    def test_no_backfill_when_missing_frozen_signal_history(self):
        data=candles()
        data.pop(58*BAR)
        result=self.run_step(60,data)["markets"]["BTC-USD"]
        self.assertEqual(result["status"],"DATA_UNAVAILABLE")
        self.assertFalse(result["promotion_authorized"])

    def test_midstream_candle_gap_blocks_unrealized_paper_profit(self):
        self.run_step(60)
        history=candles()
        del history[61*BAR]
        result=self.run_step(63,history)["markets"]["BTC-USD"]
        self.assertEqual(result["status"],"DATA_GAP")
        self.assertFalse(result["promotion_authorized"])

    def test_historical_results_do_not_create_prospective_return(self):
        # Frozen candidate's prior historical win does not count as earned
        # prospective profit before a genuinely future six-hour open.
        self.manifest["candidates"][0]["historical_holdout_net"]=.55
        self.save()
        result=self.run_step(60)["markets"]["BTC-USD"]
        self.assertEqual(result["fee_net_paper_return"],0.)
        self.assertEqual(result["status"],"COLLECTING")


if __name__ == "__main__":
    unittest.main()
