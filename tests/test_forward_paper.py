"""No real orders; test causal forward paper decisions with deterministic bars."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.bean_five_minute import BAR
from src.forward_paper import observe_and_score, freeze_training_candidates, freeze
from scripts.forward_paper import main as forward_paper_cli_main

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

    def test_default_forward_paper_fee_is_90bps_and_persisted(self):
        self.simulate_tick(440)
        with closing(sqlite3.connect(self.path)) as db:
            fee, spread = db.execute(
                "SELECT fee_bps,spread_bps FROM frozen_policy WHERE product=?",
                ("BTC-USD",)).fetchone()
        self.assertEqual((fee, spread), (90.0, 10.0))

    def test_nonfinite_costs_cannot_mutate_frozen_fee_net_paper_ledger(self):
        self.simulate_tick(440)
        with closing(sqlite3.connect(self.path)) as db:
            original = db.execute(
                "SELECT fee_bps,spread_bps FROM frozen_policy").fetchone()
            original_decisions = db.execute(
                "SELECT COUNT(*) FROM paper_decisions").fetchone()[0]
            for invalid in (float("nan"), float("inf"), float("-inf")):
                for field in ("fee_bps", "spread_bps"):
                    with self.subTest(invalid=str(invalid), field=field):
                        with self.assertRaisesRegex(ValueError, "Invalid modeled trading costs"):
                            self.simulate_tick(441, **{field: invalid})
                        # freeze() is also public and must not bypass validation.
                        with self.assertRaisesRegex(ValueError, "Invalid modeled trading costs"):
                            freeze(db, product="BTC-USD", policy="trend",
                                   now=441*BAR+1,
                                   fee_bps=invalid if field == "fee_bps" else 90,
                                   spread_bps=invalid if field == "spread_bps" else 10)
            self.assertEqual(db.execute(
                "SELECT fee_bps,spread_bps FROM frozen_policy").fetchone(), original)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM paper_decisions").fetchone()[0],
                original_decisions)

    def test_cli_uses_same_90bps_default_without_live_exchange(self):
        from unittest.mock import patch
        manifest_path = Path(self.folder.name)/"cli-manifest.json"
        manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")
        with patch("scripts.forward_paper.observe_and_score",
                   return_value={"paper_only": True}) as observer:
            self.assertEqual(forward_paper_cli_main([
                "--manifest", str(manifest_path), "--db", str(self.path)]), 0)
        self.assertEqual(observer.call_args.kwargs["fee_bps"], 90.0)
        self.assertEqual(observer.call_args.kwargs["spread_bps"], 10.0)

    def test_freeze_uses_training_rank_only_and_never_chooses_holdout_winner(self):
        report={"schema":"aiscend.universe_research_aggregate.v1",
                "promotion_authorized":False,
                "training_preselected":[
                    {"product":"BTC-USD","policy":"trend","train_return":.08,
                     "holdout_return":-.50},
                    {"product":"ETH-USDC","policy":"breakout","train_return":.07,
                     "holdout_return":.99},
                ]}
        frozen=freeze_training_candidates(report,limit=2)
        self.assertEqual(frozen[0]["product"],"BTC-USD")
        self.assertEqual(frozen[0]["policy"],"trend")
        self.assertEqual(frozen[1]["product"],"ETH-USDC")
        self.assertTrue(all(x["training_only_frozen"] for x in frozen))
        self.assertEqual(len(frozen[0]["implementation_sha"]), 64)
        self.assertEqual(frozen[0]["implementation_sha"],
                         frozen[1]["implementation_sha"])
        report["promotion_authorized"]=True
        with self.assertRaises(ValueError):
            freeze_training_candidates(report)

    def test_signal_is_recorded_before_next_entry_bar_exists(self):
        result=self.simulate_tick(440)
        self.assertTrue(result["paper_only"])
        self.assertFalse(result["promotion_authorized"])
        signal=result["products"]["BTC-USD"]
        self.assertEqual(signal["status"],"PENDING")
        self.assertEqual(signal["signal_ts"],439*BAR)
        self.assertEqual(signal["entry_ts"],441*BAR)
        self.assertEqual(signal["exit_ts"],513*BAR)
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

    def test_frozen_ledger_rejects_strategy_revision_without_mixing_returns(self):
        self.simulate_tick(440)
        with closing(sqlite3.connect(self.path)) as db:
            original = db.execute(
                "SELECT policy_sha FROM frozen_policy").fetchone()[0]
            count = db.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0]
        with patch("src.forward_paper._implementation_sha", return_value="f"*64):
            with self.assertRaisesRegex(ValueError, "Frozen strategy/cost assumptions"):
                self.simulate_tick(441)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute(
                "SELECT policy_sha FROM frozen_policy").fetchone()[0], original)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM paper_decisions").fetchone()[0], count)

    def test_frozen_manifest_rejects_changed_strategy_before_first_signal(self):
        report = {"schema":"aiscend.universe_research_aggregate.v1",
                  "promotion_authorized":False,
                  "training_preselected":[{"product":"BTC-USD","policy":"trend"}]}
        manifest = freeze_training_candidates(report)
        with patch("src.forward_paper._implementation_sha", return_value="0"*64):
            with self.assertRaisesRegex(
                    ValueError, "Frozen manifest strategy implementation changed"):
                observe_and_score(
                    manifest=manifest, db_path=self.path, fetcher=self.fetch,
                    clock=lambda:440*BAR+1)
        self.assertFalse(self.path.exists())

    def test_no_signals_on_incomplete_initial_lookback(self):
        short=lambda p,s,e:{t:v for t,v in self.rows.items()
                            if 430*BAR<=t<e}
        r=observe_and_score(manifest=MANIFEST,db_path=self.path,
                            fetcher=short,clock=lambda:440*BAR+1)
        self.assertEqual(r["products"]["BTC-USD"]["status"],"NO_SIGNAL")

    def test_entry_follows_observation_even_at_end_of_current_slot(self):
        now = 441*BAR-1
        signal = observe_and_score(
            manifest=MANIFEST, db_path=self.path, fetcher=self.fetch,
            clock=lambda: now)["products"]["BTC-USD"]
        self.assertEqual(signal["status"], "PENDING")
        self.assertGreater(signal["entry_ts"], now)
        self.assertEqual(signal["entry_ts"], 441*BAR)
        # A past opening price must not enter the score calculation.
        self.rows[440*BAR]["open"] = 0.000001
        scored = self.simulate_tick(515)
        self.assertEqual(scored["products"]["BTC-USD"]["status"],"SCORED")
        with closing(sqlite3.connect(self.path)) as db:
            entry,entry_ts,observed = db.execute(
                "SELECT entry_price,entry_ts,observed_at FROM paper_decisions "
                "WHERE state='SCORED'").fetchone()
        self.assertGreater(entry_ts, observed)
        self.assertEqual(entry, self.rows[441*BAR]["open"])

    def test_slow_fetch_crossing_entry_open_never_creates_paper_fill(self):
        # The initial clock is still inside the signal slot, but fetching
        # historical bars takes us past the planned next-candle opening.
        clock_value=[441*BAR-1]
        def slow_fetch(product,start,end):
            rows=self.fetch(product,start,end)
            clock_value[0]=441*BAR+1
            return rows
        result=observe_and_score(
            manifest=MANIFEST,db_path=self.path,fetcher=slow_fetch,
            clock=lambda:clock_value[0])
        decision=result["products"]["BTC-USD"]
        self.assertEqual(decision["status"],"NONCAUSAL_ENTRY")
        self.assertEqual(result["scorecard"]["BTC-USD"]["scored_trades"],0)
        with closing(sqlite3.connect(self.path)) as db:
            state,observed,entry,net=db.execute(
                "SELECT state,observed_at,entry_ts,net_return "
                "FROM paper_decisions").fetchone()
        self.assertEqual(state,"NONCAUSAL_ENTRY")
        self.assertGreaterEqual(observed,entry)
        self.assertIsNone(net)

    def test_existing_legacy_scores_are_quarantined_not_profitable(self):
        self.simulate_tick(440)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE paper_decisions SET entry_ts=NULL,state='SCORED',"
                "entry_price=1,exit_price=100,net_return=99")
            db.commit()
        result = self.simulate_tick(515)
        self.assertEqual(result["scorecard"]["BTC-USD"]["scored_trades"],0)
        self.assertEqual(result["scorecard"]["BTC-USD"]["net_return"],0)
        with closing(sqlite3.connect(self.path)) as db:
            state,entry,ret=db.execute(
                "SELECT state,entry_price,net_return FROM paper_decisions "
                "WHERE signal_ts=?", (439*BAR,)).fetchone()
        self.assertEqual(state,"NONCAUSAL_ENTRY")
        self.assertIsNone(entry)
        self.assertIsNone(ret)

    def test_old_schema_is_migrated_without_trusting_historical_returns(self):
        self.simulate_tick(440)
        with closing(sqlite3.connect(self.path)) as db:
            # Emulate an existing pre-repair on-disk ledger, with no entry_ts.
            db.execute("ALTER TABLE paper_decisions DROP COLUMN entry_ts")
            db.execute(
                "UPDATE paper_decisions SET state='SCORED',"
                "entry_price=1,exit_price=100,net_return=99")
            db.commit()
        self.simulate_tick(515)
        with closing(sqlite3.connect(self.path)) as db:
            columns={row[1] for row in db.execute("PRAGMA table_info(paper_decisions)")}
            state,net=db.execute(
                "SELECT state,net_return FROM paper_decisions "
                "WHERE signal_ts=?", (439*BAR,)).fetchone()
        self.assertIn("entry_ts",columns)
        self.assertEqual(state,"NONCAUSAL_ENTRY")
        self.assertIsNone(net)

    def test_quarantined_same_slot_is_not_reported_as_new_position(self):
        self.simulate_tick(440)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE paper_decisions SET entry_ts=NULL")
            db.commit()
        result=self.simulate_tick(440)
        self.assertEqual(result["products"]["BTC-USD"]["status"],"NONCAUSAL_ENTRY")
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute("SELECT state FROM paper_decisions").fetchall()
        self.assertEqual(rows,[("NONCAUSAL_ENTRY",)])

    def test_freeze_manifest_cli_writes_parseable_newline_terminated_json(self):
        aggregate=Path(self.folder.name)/"aggregate.json"
        manifest=Path(self.folder.name)/"manifest.json"
        aggregate.write_text(json.dumps({
            "schema":"aiscend.universe_research_aggregate.v1",
            "promotion_authorized":False,
            "training_preselected":[{"product":"BTC-USD","policy":"trend"}]
        }),encoding="utf-8")
        self.assertEqual(forward_paper_cli_main([
            "--freeze-aggregate",str(aggregate),"--output",str(manifest)]),0)
        written=manifest.read_text(encoding="utf-8")
        self.assertTrue(written.endswith("\n"))
        parsed=json.loads(written)
        self.assertEqual(parsed[0]["product"],"BTC-USD")
        self.assertEqual(len(parsed[0]["implementation_sha"]),64)


if __name__=="__main__":
    unittest.main()
