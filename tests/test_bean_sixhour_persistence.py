"""Persistent prospective BEAN SQLite ledger: restart, integrity and backup tests.

All market candles are offline fixtures. No Coinbase credentials or orders.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.bean_sixhour_forward import (
    inspect, verify_database, backup_database, persistent_db_path, watch,
)
from src.bean_six_hour_universe import BAR


def offline_history():
    bars = {}
    close = 100.
    for i in range(200):
        opening = close
        close *= 1.001
        ts = i * BAR
        bars[ts] = {"ts": ts, "open": opening, "close": close, "volume": 10.}
    return bars


class PersistentProspectiveSQLiteTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.manifest = self.root / "frozen.json"
        self.db = self.root / "paper.sqlite3"
        self.manifest.write_text(json.dumps({
            "schema": "aiscend.bean.sixhour.prospective.freeze.v1",
            "promotion_authorized": False,
            "frozen_at_unix": 50 * BAR + 600,
            "fee_bps_per_side": 60,
            "spread_bps_roundtrip": 10,
            "min_forward_completed_bars": 120,
            "min_realized_roundtrips": 5,
            "min_excess_return_vs_cash": .02,
            "min_excess_return_vs_buy_hold": .02,
            "max_forward_drawdown": .20,
            "candidates": [{
                "product": "BTC-USD", "policy": "bean_q",
                "q_seed": {"up/calm": {"long": 100., "cash": 0.}},
            }],
        }), encoding="utf-8")
        self.market = offline_history()

    def observe(self, n):
        return inspect(self.manifest, self.db,
                       clock=lambda: n * BAR + 600,
                       history_fetcher=lambda _product, **_kwargs: self.market)

    def test_real_sqlite_state_survives_repeated_disconnected_processes(self):
        self.observe(60)
        # Two separate Python processes reading/writing exactly one SQLite file.
        script = (
            "import json,sys\n"
            "from pathlib import Path\n"
            "from src.bean_sixhour_forward import inspect\n"
            "from src.bean_six_hour_universe import BAR\n"
            "history={}\n"
            "p=100.\n"
            "for i in range(200):\n"
            "  old=p; p*=1.001; t=i*BAR\n"
            "  history[t]={'ts':t,'open':old,'close':p,'volume':10.}\n"
            "result=inspect(Path(sys.argv[1]),Path(sys.argv[2]),"
            "clock=lambda:int(sys.argv[3])*BAR+600,"
            "history_fetcher=lambda _product,**kwargs:history)\n"
            "print(json.dumps(result))\n"
        )
        results = []
        for n in (62, 62, 63):
            proc = subprocess.run(
                [sys.executable, "-c", script, str(self.manifest),
                 str(self.db), str(n)],
                capture_output=True, text=True, timeout=25, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            results.append(json.loads(proc.stdout))
        self.assertEqual(results[0]["ledger_tip"], results[1]["ledger_tip"])
        self.assertNotEqual(results[1]["ledger_tip"], results[2]["ledger_tip"])
        verified = verify_database(self.manifest, self.db)
        self.assertEqual(verified["status"], "LEDGER_VERIFIED")
        self.assertGreaterEqual(verified["markets"]["BTC-USD"]["orders"], 1)
        self.assertFalse(verified["promotion_authorized"])

    def test_live_sqlite_backup_is_verified_and_does_not_overwrite(self):
        self.observe(60)
        self.observe(62)
        archive = self.root / "backups" / "snapshot.sqlite3"
        result = backup_database(self.manifest, self.db, archive)
        self.assertEqual(result["status"], "BACKUP_VERIFIED")
        self.assertEqual(result["ledger_tip"],
                         verify_database(self.manifest, archive)["ledger_tip"])
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                         result["sha256"])
        self.assertTrue(archive.with_suffix(".sqlite3.sha256").exists())
        with self.assertRaises(FileExistsError):
            backup_database(self.manifest, self.db, archive)

    def test_tampered_ledger_checkpoint_fails_closed(self):
        self.observe(60)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE meta SET v=? WHERE k='ledger_tip'", ("a"*64,))
        with self.assertRaises(ValueError):
            verify_database(self.manifest, self.db)
        with self.assertRaises(ValueError):
            self.observe(62)

    def test_deleted_tail_event_fails_checkpoint_recovery(self):
        self.observe(60)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DELETE FROM events WHERE seq=(SELECT MAX(seq) FROM events)")
        with self.assertRaises(ValueError):
            verify_database(self.manifest, self.db)

    def test_pending_orders_and_q_are_unchanged_on_idempotent_revisit(self):
        first = self.observe(60)
        second = self.observe(60)
        self.assertEqual(first["ledger_tip"], second["ledger_tip"])
        self.assertEqual(first["markets"], second["markets"])

    def test_simultaneous_python_writers_are_serialized(self):
        self.observe(60)
        with ThreadPoolExecutor(max_workers=2) as workers:
            outputs = list(workers.map(self.observe, [62, 62]))
        self.assertEqual(outputs[0]["ledger_tip"], outputs[1]["ledger_tip"])
        verified = verify_database(self.manifest, self.db)
        self.assertEqual(verified["ledger_tip"], outputs[0]["ledger_tip"])

    def test_powershell_watch_reuses_one_file_and_takes_snapshots(self):
        ticks = [60*BAR+600, 62*BAR+600]
        counter = [0]
        reports = []
        def sleep(_seconds):
            counter[0] += 1
        def clock():
            return ticks[counter[0]]
        def observer(m, d, *, clock):
            return inspect(m, d, clock=clock,
                           history_fetcher=lambda _p, **_kw: self.market)
        count = watch(self.manifest, self.db,
                      clock=clock, sleep=sleep, observer=observer,
                      max_cycles=2, backup_dir=self.root/"snapshots",
                      reporter=reports.append)
        self.assertEqual(count, 2)
        self.assertEqual(len(reports), 2)
        self.assertTrue(list((self.root/"snapshots").glob("*.sqlite3")))
        self.assertEqual(json.loads(reports[-1])["ledger_tip"],
                         verify_database(self.manifest, self.db)["ledger_tip"])

    def test_default_sqlite_is_outside_git_checkout(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            result = persistent_db_path()
        self.assertEqual(result.parent, self.root / "AutonomousCapitalLab")
        self.assertEqual(result.suffix, ".sqlite3")

    def test_verify_only_does_not_create_a_missing_db(self):
        with self.assertRaises(FileNotFoundError):
            verify_database(self.manifest, self.db)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
