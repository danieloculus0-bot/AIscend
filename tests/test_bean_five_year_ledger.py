"""Offline durability and tamper regressions for five-year BEAN event memory."""
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.bean_five_year_walkforward import walkforward
from scripts.bean_five_year_ledger import store_report, audit_db, validate_experiment
from tests.test_bean_five_year_walkforward import synthetic


class FiveYearMemoryTests(unittest.TestCase):
    def _payload(self):
        return {"markets":{"TEST-USD":walkforward(
            synthetic(2100,shape="waves"),product="TEST-USD",
            train_bars=360,test_bars=120)}}

    def test_every_event_and_loss_persists_and_replays(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"bean.sql"
            report=self._payload()
            one=store_report(db,report)
            self.assertEqual(one["markets"][0]["status"],"STORED")
            verified=audit_db(db)
            self.assertEqual(verified["status"],"LEDGER_VERIFIED")
            self.assertEqual(verified["experiments"][0]["net_return"],
                             report["markets"]["TEST-USD"]["net_return"])
            with closing(sqlite3.connect(db)) as sql:
                count=sql.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(count,len(report["markets"]["TEST-USD"]["ledger"]))
            duplicate=store_report(db,report)
            self.assertEqual(duplicate["markets"][0]["status"],"ALREADY_RECORDED")
            with closing(sqlite3.connect(db)) as sql:
                self.assertEqual(count,sql.execute(
                    "SELECT COUNT(*) FROM events").fetchone()[0])

    def test_unmodified_report_cannot_authorize_funded_orders(self):
        payload=self._payload()
        record=payload["markets"]["TEST-USD"]
        self.assertFalse(record["promotion_authorized"])
        record["promotion_authorized"]=True
        with self.assertRaisesRegex(ValueError,"INVALID_RESEARCH_AUTHORITY"):
            validate_experiment("TEST-USD",record)

    def test_missing_or_changed_experiment_fails_audit(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"bean.sql"
            store_report(db,self._payload())
            with closing(sqlite3.connect(db)) as sql:
                with sql:
                    sql.execute("UPDATE events SET event_json='{}' WHERE sequence=0")
            with self.assertRaisesRegex(ValueError,"BEAN_EVENT_REPLAY_CHANGED"):
                audit_db(db)

    def test_normal_partial_never_entered_as_winning_trade(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"bean.sql"
            r=store_report(db,{"markets":{"X-USD":{
                "status":"PARTIAL","promotion_authorized":False}}})
            self.assertFalse(r["markets"][0]["stored"])
            self.assertEqual(audit_db(db)["experiments"],[])


if __name__=="__main__":
    unittest.main()
