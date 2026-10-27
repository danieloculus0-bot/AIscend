"""Offline contracts for durable scheduled Coinbase public-paper observations."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import bean_forward_hosted as hosted
from src.bean_sixhour_forward import (
    inspect as actual_inspect, verify_database,
)
from src.bean_six_hour_universe import BAR


class HostedProspectiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = Path(__file__).resolve().parents[1]
        self.state = self.root / "state"
        self.manifest = self.root / "freeze.json"
        self.manifest.write_text(json.dumps({
            "schema": "aiscend.bean.sixhour.prospective.freeze.v1",
            "frozen_at_unix": 50 * BAR + 600,
            "promotion_authorized": False,
            "fee_bps_per_side": 90,
            "spread_bps_roundtrip": 10,
            "min_forward_completed_bars": 120,
            "min_realized_roundtrips": 5,
            "min_excess_return_vs_cash": .02,
            "min_excess_return_vs_buy_hold": .02,
            "max_forward_drawdown": .2,
            "candidates": [{"product": "BTC-USD", "policy": "bean_q",
                            "q_seed": {"up/calm": {"long": 20., "cash": 0.}}}],
        }))
        self.candles = {}
        price = 100.
        for i in range(200):
            old = price
            price *= 1.001
            self.candles[i * BAR] = {
                "ts": i * BAR, "open": old,
                "close": price, "volume": 10.,
            }

    def run_observation(self, cycle, n):
        def frozen_observer(manifest, database):
            return actual_inspect(manifest, database,
                                  clock=lambda: n * BAR + 600,
                                  history_fetcher=lambda _product, **kwargs: self.candles)
        with patch.object(hosted, "inspect", side_effect=frozen_observer):
            return hosted.collect(self.state, self.manifest, self.source,
                                  "test-" + str(cycle),
                                  self.root / ("backup-" + str(cycle) + ".sqlite3"))

    def test_retains_same_sqlite_across_runs_and_every_report_has_verified_tip(self):
        first = self.run_observation(1, 60)
        second = self.run_observation(2, 62)
        self.assertNotEqual(first["ledger_tip"], second["ledger_tip"])
        self.assertEqual(first["source_sha256"], second["source_sha256"])
        self.assertEqual(first["manifest_sha"], second["manifest_sha"])
        self.assertEqual(set(first["markets"]), {"BTC-USD"})
        self.assertFalse(second["funded_orders"])
        self.assertFalse(second["promotion_authorized"])
        self.assertEqual(second["ledger_tip"],
                         verify_database(self.manifest,
                             self.state / "evidence/prospective.sqlite3")["ledger_tip"])
        self.assertGreater(second["markets"]["BTC-USD"]["forward_bars"], 0)
        for i, entry in enumerate((first, second), start=1):
            self.assertEqual(json.loads(
                (self.state / ("reports/test-" + str(i) + ".json")).read_text()),
                entry)
            self.assertTrue((self.root / ("backup-" + str(i) + ".sqlite3")).exists())
            self.assertTrue((self.root / ("backup-" + str(i) + ".sqlite3.sha256")).exists())

    def test_missing_pinned_database_refuses_silent_restart(self):
        first = self.run_observation(1, 60)
        (self.state / "evidence/prospective.sqlite3").unlink()
        with self.assertRaisesRegex(ValueError, "database missing"):
            self.run_observation(2, 62)
        self.assertEqual(json.loads(
            (self.state / "evidence/latest.json").read_text())["ledger_tip"],
            first["ledger_tip"])
        self.assertFalse((self.state / "reports/test-2.json").exists())

    def test_older_valid_sqlite_backup_cannot_rollback_history(self):
        first = self.run_observation(1, 60)
        second = self.run_observation(2, 62)
        database = self.state / "evidence/prospective.sqlite3"
        # Simulate restoring an internally valid but stale SQLite backup.
        for suffix in ("-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
        shutil.copyfile(self.root / "backup-1.sqlite3", database)
        self.assertEqual(verify_database(self.manifest, database)["ledger_tip"],
                         first["ledger_tip"])
        with self.assertRaisesRegex(ValueError, "continuity mismatch"):
            self.run_observation(3, 64)
        self.assertEqual(json.loads(
            (self.state / "evidence/latest.json").read_text())["ledger_tip"],
            second["ledger_tip"])
        self.assertFalse((self.state / "reports/test-3.json").exists())

    def test_missing_latest_report_cannot_adopt_unanchored_database(self):
        self.run_observation(1, 60)
        (self.state / "evidence/latest.json").unlink()
        with self.assertRaisesRegex(ValueError, "no retained latest report"):
            self.run_observation(2, 62)

    def test_reused_run_identity_cannot_overwrite_evidence(self):
        first = self.run_observation(1, 60)
        with self.assertRaises(FileExistsError):
            self.run_observation(1, 62)
        self.assertEqual(first["ledger_tip"],
            verify_database(self.manifest,
                self.state / "evidence/prospective.sqlite3")["ledger_tip"])

    def test_engine_mutation_blocks_before_observation(self):
        source = self.root / "source"
        source.mkdir()
        (source / "algorithm.py").write_text("rule=1")
        d = self.root / "pin"
        initial = hosted.pin_source(d, source, files=("algorithm.py",))
        self.assertEqual(len(initial), 64)
        (source / "algorithm.py").write_text("rule=2")
        with self.assertRaisesRegex(ValueError, "engine changed"):
            hosted.pin_source(d, source, files=("algorithm.py",))

    def test_unpinned_existing_database_is_not_adopted(self):
        source = self.root / "source"
        source.mkdir()
        (source / "algorithm.py").write_text("rule=1")
        d = self.root / "pin"
        d.mkdir()
        (d / "prospective.sqlite3").write_bytes(b"untracked")
        with self.assertRaisesRegex(ValueError, "missing its engine pin"):
            hosted.pin_source(d, source, files=("algorithm.py",))

    def test_no_unexpected_promotion_or_duplicate_candidates(self):
        invalid = json.loads(self.manifest.read_text())
        invalid["promotion_authorized"] = True
        self.manifest.write_text(json.dumps(invalid))
        with self.assertRaises(ValueError):
            hosted.collect(self.state, self.manifest, self.source, "test-1",
                           self.root / "backup.sqlite3")
        self.assertFalse((self.state / "evidence/prospective.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
