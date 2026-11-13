"""Persistent BEAN tests: archive every market and recall without future leakage."""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from scripts.bean_five_year_archive import (
    create_archive, recall_archive, verify_archive, FILES
)
from scripts.bean_five_year_ledger import store_report
from scripts.bean_five_year_recall import recall_prior
from scripts.bean_five_year_walkforward import walkforward, BAR, WARMUP
from src.universe_research import _catalog_hash


def history(n):
    raw = {}
    for i in range(n):
        px = 100 + i * .15 + 12 * math.sin(i / 26)
        raw[i*BAR] = {"ts": i*BAR, "open": px, "high": px+1,
                      "low": px-1, "close": px, "volume": 100}
    return raw


def prepare(directory):
    root = Path(directory)
    prod = "ETH-USD"
    old = walkforward(history(1300), product=prod,
                      train_bars=360, test_bars=120)
    db = root/"bean-5y-universe.sqlite3"
    store_report(db, {"markets": {prod: old, "NEW-USD": {
        "status": "INSUFFICIENT_HISTORY", "promotion_authorized": False}}})
    markets = ["ETH-USD", "NEW-USD"]
    catalog_hash = _catalog_hash(markets)
    catalog = root/"bean-5y-catalog.json"
    catalog.write_text(json.dumps({
        "schema": "aiscend.bean.five_year.catalog.v1",
        "catalog_hash": catalog_hash, "eligible_quote_markets": markets,
        "count": 2, "promotion_authorized": False
    }), encoding="utf-8")
    summary = root/"bean-5y-universe-summary.json"
    summary.write_text(json.dumps({
        "schema": "aiscend.bean.five_year.universe.aggregate.v1",
        "catalog_hash": catalog_hash, "attempted_count": 2,
        "promotion_authorized": False
    }), encoding="utf-8")
    return prod, old, db, catalog, summary


class BeanFiveYearArchiveRecallTests(unittest.TestCase):
    def test_complete_archive_includes_losses_and_insufficient_history(self):
        with tempfile.TemporaryDirectory() as folder:
            prod, old, db, catalog, summary = prepare(folder)
            destination = Path(folder)/"archive.zip"
            saved = create_archive(catalog=catalog, summary=summary,
                                   ledger=db, output=destination)
            verified = verify_archive(destination)
            self.assertEqual(saved["outcome_count"], 2)
            self.assertEqual(verified["experiment_count"], 1)
            self.assertEqual(verified["zip_sha256"], saved["zip_sha256"])
            # Archive bytes are deterministic for a fixed, unchanged database.
            again = create_archive(catalog=catalog, summary=summary,
                                   ledger=db, output=destination)
            self.assertEqual(again["zip_sha256"], saved["zip_sha256"])
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()),
                                 {*FILES, "manifest.json"})

    def test_recalled_lessons_available_only_after_source_history_ends(self):
        with tempfile.TemporaryDirectory() as folder:
            prod, old, db, catalog, summary = prepare(folder)
            dest = Path(folder)/"archive.zip"
            create_archive(catalog=catalog, summary=summary, ledger=db,
                           output=dest)
            end = old["ledger"][0]["end"]
            premature = recall_archive(dest, product=prod,
                                       before_ts=end)
            self.assertEqual(premature["status"], "NO_ELIGIBLE_PRIOR")
            valid = recall_archive(dest, product=prod,
                                   before_ts=end+BAR)
            self.assertEqual(valid["status"], "RECALLED_CAUSAL_EVIDENCE")
            self.assertEqual(len(valid["source_experiments"]), 1)
            self.assertEqual(len(valid["prior_scores"]), 7)
            duplicate = recall_archive(dest, product=prod,
                                       before_ts=end+BAR)
            self.assertEqual(valid, duplicate)
            other = recall_archive(dest, product="BTC-USD",
                                   before_ts=end+BAR)
            self.assertEqual(other["status"], "NO_ELIGIBLE_PRIOR")

    def test_old_db_changes_are_detected_before_recall(self):
        with tempfile.TemporaryDirectory() as folder:
            prod, old, db, catalog, summary = prepare(folder)
            with closing(sqlite3.connect(db)) as con:
                with con:
                    con.execute("UPDATE events SET event_json='{}' WHERE sequence=0")
            with self.assertRaisesRegex(ValueError, "BEAN_EVENT_REPLAY_CHANGED"):
                recall_prior(db, product=prod,
                             before_ts=old["ledger"][0]["end"]+BAR)

    def test_archive_file_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            prod, old, db, catalog, summary = prepare(folder)
            dest = Path(folder)/"archive.zip"
            create_archive(catalog=catalog, summary=summary, ledger=db,
                           output=dest)
            with zipfile.ZipFile(dest, "a") as z:
                z.writestr("extraneous.txt", "injected data")
            with self.assertRaisesRegex(ValueError, "ARCHIVE_UNEXPECTED_FILES"):
                verify_archive(dest)

    def test_archived_priors_seed_only_nonoverlapping_future_simulation(self):
        with tempfile.TemporaryDirectory() as folder:
            prod, old, db, catalog, summary = prepare(folder)
            replay = walkforward(history(2500), product=prod, recall_db=db)
            self.assertEqual(replay["recalled_prior_status"],
                             "RECALLED_CAUSAL_EVIDENCE")
            self.assertEqual(len(replay["recalled_prior_sources"]), 1)
            old_end = old["ledger"][0]["end"]
            self.assertLess(old_end, replay["folds"][0]["test_start"])
            first_recall = next(x for x in replay["ledger"]
                                if x["type"] == "research_memory_recall")
            self.assertEqual(first_recall["status"],
                             "RECALLED_CAUSAL_EVIDENCE")
            # A same-interval prior would contaminate historical holdout.
            overlapping = walkforward(history(2500), product=prod)
            other_db = Path(folder)/"overlap.sqlite3"
            store_report(other_db, {"markets": {prod: overlapping}})
            rerun = walkforward(history(2500), product=prod, recall_db=other_db)
            self.assertEqual(rerun["recalled_prior_status"],
                             "NO_ELIGIBLE_PRIOR")

if __name__ == "__main__":
    unittest.main()
