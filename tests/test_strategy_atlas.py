from __future__ import annotations
import copy
import gzip
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.strategy_atlas import Atlas,MissingFeature,append_experiment,build_database,canonical,compress_database,evaluate_expression,evaluate_recipe,expression_names,recall_experiments,validate_recipe
from src.narrative_evidence import document,extract_headline,ingest_documents,narrative_context,parse_feed

ROOT=Path(__file__).resolve().parents[1]/"research"/"strategy_atlas"

class AtlasTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=self.root/"test.sqlite3"
        for name in ("sources.jsonl","strategies.jsonl","datasets.jsonl"):(self.root/name).write_bytes((ROOT/name).read_bytes())
        build_database(self.root,self.db)
    def tearDown(self):self.temp.cleanup()
    def test_catalog_refs_and_algorithms(self):
        with Atlas(self.db) as a:
            self.assertGreaterEqual(a.stats()["strategies"],218)
            self.assertGreaterEqual(len(a.stats()["families"]),19)
            for r in a.query():validate_recipe(r)
            self.assertEqual(a.con.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertTrue(any("negative" in r[0] for r in a.con.execute("SELECT result FROM sources")))
            self.assertTrue(a.query(text="martingale"))
    def test_no_future_or_missing_features(self):
        with Atlas(self.db) as a:r=a.recipe("sma_cross")
        result=evaluate_recipe(r,{"fast":12,"slow":10},decision_at=100,feature_available_at={"fast":90,"slow":90})
        self.assertEqual(result["target_direction"],1);self.assertFalse(result["trade_authorized"])
        with self.assertRaises(ValueError):evaluate_recipe(r,{"fast":12,"slow":10},decision_at=89,feature_available_at={"fast":90,"slow":90})
        with self.assertRaises(MissingFeature):evaluate_recipe(r,{"fast":12},decision_at=100,feature_available_at={"fast":90,"slow":90})
        with self.assertRaises(MissingFeature):evaluate_recipe(r,{"fast":float("nan"),"slow":10},decision_at=100,feature_available_at={"fast":90,"slow":90})
    def test_snapshot_exits_and_bearish_direction(self):
        with Atlas(self.db) as a:r=a.recipe("sma_cross");candle=a.recipe("pattern_cdlengulfing")
        out=evaluate_recipe(r,{"fast":8,"slow":10},decision_at=100,feature_available_at={"fast":90,"slow":90},position=1)
        self.assertEqual(out["target_direction"],0)
        out=evaluate_recipe(candle,{"pattern_score":-100,"opposite_pattern":0,"held_bars":0},decision_at=100,feature_available_at={"pattern_score":90,"opposite_pattern":90,"held_bars":90})
        self.assertEqual(out["target_direction"],-1)
    def test_specialized_engine_is_explicit(self):
        with Atlas(self.db) as a:r=a.recipe("cash_carry")
        self.assertEqual(evaluate_recipe(r,{},decision_at=100,feature_available_at={})["status"],"SPECIALIZED_ENGINE_REQUIRED")
    def test_untrusted_rules_cannot_execute_code(self):
        for s in ["__import__('os').system('id')","x.__class__","x[0]","(lambda: 1)()","[x for x in y]","1e999","2**1000000"]:
            with self.subTest(s=s),self.assertRaises((ValueError,SyntaxError)):expression_names(s)
        self.assertEqual(evaluate_expression("max(x,2) + abs(y)",{"x":3,"y":-4}),7)
    def test_readonly_compressed_snapshot(self):
        compressed=self.root/"snapshot.sqlite3.gz";receipt=compress_database(self.db,compressed)
        self.assertEqual(receipt["sha256"],hashlib.sha256(compressed.read_bytes()).hexdigest())
        with Atlas(compressed) as a:
            self.assertEqual(a.recipe("cash")["family"],"baseline")
            with self.assertRaises(sqlite3.OperationalError):a.con.execute("DELETE FROM sources")
    def test_history_roundtrip_and_tamper(self):
        h=self.root/"tiny.jsonl.gz"
        records=[{"asset":"btc","time":"2025-01-01T00:00:00+00:00","values":{"ReferenceRateUSD":"100","TxCnt":"123"}}, {"asset":"btc","time":"2025-01-02T00:00:00+00:00","values":{"ReferenceRateUSD":"101"}}]
        h.write_bytes(gzip.compress("".join(canonical(r)+"\n" for r in records).encode(),mtime=0))
        manifest={"assets":[{"asset":"btc","path":h.name,"sha256":hashlib.sha256(h.read_bytes()).hexdigest(),"rows":2,"metrics":["ReferenceRateUSD","TxCnt"],"first_time":records[0]["time"],"last_time":records[-1]["time"]}]}
        (self.root/"history_manifest.json").write_text(canonical(manifest));build_database(self.root,self.db)
        with Atlas(self.db) as a:
            self.assertEqual(len(list(a.history("btc"))),2)
            self.assertEqual(list(a.history("btc",metric="TxCnt"))[0]["value"],"123")
            self.assertEqual(len(list(a.history("btc",start="2025-01-02T00:00:00Z"))),1)
        h.write_bytes(b"modified")
        with self.assertRaises(ValueError):build_database(self.root,self.root/"bad.sqlite3")
    def test_documents_no_historical_backdating(self):
        r=document("Bitcoin ETF approved","https://news.example/article?utm_source=foo",published_at=10,first_seen_at=100)
        self.assertEqual(ingest_documents(self.db,[r,r]),1)
        with Atlas(self.db) as a:
            self.assertFalse(narrative_context(a,as_of=99)["narrative_available"])
            self.assertTrue(narrative_context(a,as_of=100)["narrative_available"])
    def test_syndication_and_opposing_evidence(self):
        rows=[document("Bitcoin ETF approved","https://one.example/a",first_seen_at=100),document("Bitcoin ETF approved!","https://two.example/copy",first_seen_at=101),document("Bitcoin ETF rejected","https://three.example/b",first_seen_at=102)]
        ingest_documents(self.db,rows)
        with Atlas(self.db) as a:r=narrative_context(a,as_of=103)
        self.assertEqual(r["raw_documents_in_window"],3)
        self.assertEqual(r["independent_headline_clusters"],2)
        self.assertTrue(next(t for t in r["themes"] if t["theme"]=="etf_institutional")["both_positive_and_negative"])
        self.assertEqual(len(r["evidence"]),2)
        self.assertFalse(r["claims"]["trained_sentiment_model_used"])
    def test_entity_ambiguity_and_negation(self):
        self.assertNotIn("NEAR",extract_headline("Near the end of a difficult year")['assets'])
        self.assertIn("NEAR",extract_headline("$NEAR upgrade approved")['assets'])
        self.assertNotIn("BTC",extract_headline("Bitcoin Cash upgrade approved")['assets'])
        self.assertLess(extract_headline("Bitcoin ETF not approved")['polarity'],0)
    def test_rss_parsing_dates(self):
        data=b'<rss><channel><item><title>Bitcoin rally</title><link>https://example.com/a</link><pubDate>Fri, 23 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
        result=parse_feed(data,observed_at="2026-10-24T12:00:00Z",source_group="example.com")
        self.assertEqual(len(result),1);self.assertEqual(result[0]["first_seen_at"],"2026-10-24T12:00:00+00:00")
    def test_result_recall_keeps_losses_and_checks_hash(self):
        base={"experiment_id":"trial-1","strategy_id":"sma_cross","dataset_sha256":"a"*64,"config":{"fee_bps":90},"split":{"train_end":10,"test_start":11},"started_at":20,"finished_at":30,"observed_at":40,"result":"NEGATIVE","metrics":{"net_return":-0.1},"reason_code":"UN","evidence_ids":[]}
        append_experiment(self.db,base)
        self.assertEqual(recall_experiments(self.db,as_of=39),[])
        self.assertEqual(recall_experiments(self.db,as_of=40)[0]["result"],"NEGATIVE")
        with sqlite3.connect(self.db) as con:
            with self.assertRaises(sqlite3.IntegrityError):con.execute("DELETE FROM experiment_events")
            con.execute("DROP TRIGGER no_experiment_update")
            con.execute("UPDATE experiment_events SET record_json='{}'")
        with self.assertRaises(ValueError):recall_experiments(self.db,as_of=100)

if __name__=="__main__":unittest.main()
