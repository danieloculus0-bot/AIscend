"""Durable versioned experiment ledger for BEAN five-year trading research.

Append-only SQLite evidence. A historical rerun with identical chain IDs
deduplicates; conflicting records are rejected. No trade order functions.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from src.bean_profit_lab import chain


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def validate_experiment(product, report):
    if report.get("status") != "HISTORICAL_WALKFORWARD_RESEARCH":
        raise ValueError("NO_COMPLETE_HISTORICAL_EXPERIMENT")
    if report.get("promotion_authorized") is not False:
        raise ValueError("INVALID_RESEARCH_AUTHORITY")
    events = report.get("ledger")
    if not isinstance(events, list) or not events:
        raise ValueError("MISSING_BEAN_LEDGER")
    if chain(events) != report.get("ledger_tip"):
        raise ValueError("CORRUPT_BEAN_LEDGER")
    for i, event in enumerate(events):
        if event.get("type") == "locked_before_test_window":
            if event["trained_through"] >= event["test_start"]:
                raise ValueError("FUTURE_INFORMATION_IN_TRAINING")
    # Verify every locked decision has its own later postclose reward event.
    locks = [e for e in events if e["type"] == "locked_before_test_window"]
    rewards = [e for e in events if e["type"] == "bean_feedback_postclose"]
    if len(locks) != len(rewards) or len(locks) != report["fold_count"]:
        raise ValueError("MISSING_POSTCLOSE_REWARDS")
    if any(l["test_end"] != r["observed_through"] for l,r in zip(locks,rewards)):
        raise ValueError("FUTURE_OR_MISSING_REWARD")
    # Content address includes full trades, negative findings, config and events.
    immutable = {k:v for k,v in report.items() if k != "research_saved_at"}
    digest = hashlib.sha256(canonical(immutable).encode()).hexdigest()
    return digest


def store_report(db_path, report):
    """Write every complete-market ledger once; never edit existing runs."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    outcomes = []
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("""CREATE TABLE IF NOT EXISTS experiments (
            experiment_sha TEXT PRIMARY KEY,
            product TEXT NOT NULL,
            model TEXT NOT NULL,
            ledger_tip TEXT NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER NOT NULL,
            report TEXT NOT NULL,
            saved_at INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            experiment_sha TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_json TEXT NOT NULL,
            PRIMARY KEY (experiment_sha,sequence),
            FOREIGN KEY (experiment_sha) REFERENCES experiments(experiment_sha))""")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        for product, record in sorted(report.get("markets", {}).items()):
            if record.get("status") != "HISTORICAL_WALKFORWARD_RESEARCH":
                outcomes.append({"product": product, "status": record.get("status"),
                                 "stored": False})
                continue
            sha = validate_experiment(product, record)
            prior = db.execute("SELECT report FROM experiments WHERE experiment_sha=?",
                               (sha,)).fetchone()
            encoded = canonical(record)
            if prior is not None:
                if prior[0] != encoded:
                    raise ValueError("IMMUTABLE_REPLAY_MISMATCH")
                outcomes.append({"product":product,"status":"ALREADY_RECORDED",
                                 "experiment_sha":sha,"stored":False})
                continue
            events = record["ledger"]
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)",
                       (sha,product,str(events[0].get("model","unknown")),
                        record["ledger_tip"],events[0]["start"],
                        events[0]["end"],encoded,int(time.time())))
            for event in events:
                db.execute("INSERT INTO events VALUES (?,?,?,?)",
                           (sha,event["seq"],event["type"],canonical(event)))
            outcomes.append({"product":product,"status":"STORED",
                             "experiment_sha":sha,
                             "events":len(events),"stored":True})
        db.commit()
    return {"status":"LEDGER_RETAINED", "db":str(path),
            "markets":outcomes,"promotion_authorized":False}


def audit_db(db_path):
    path=Path(db_path)
    if not path.is_file():
        raise FileNotFoundError("BEAN experiment SQLite ledger not initialized")
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA query_only=ON")
        ok=db.execute("PRAGMA integrity_check").fetchone()
        if not ok or ok[0]!="ok":
            raise ValueError("CORRUPT_SQLITE")
        rows=db.execute("SELECT experiment_sha,product,report FROM experiments").fetchall()
        verified=[]
        for sha, product, encoded in rows:
            record=json.loads(encoded)
            if validate_experiment(product,record)!=sha:
                raise ValueError("BEAN_EXPERIMENT_CHANGED")
            saved=[json.loads(r[0]) for r in db.execute(
                "SELECT event_json FROM events WHERE experiment_sha=? ORDER BY sequence",
                (sha,))]
            if saved!=record["ledger"]:
                raise ValueError("BEAN_EVENT_REPLAY_CHANGED")
            verified.append({"experiment_sha":sha,"product":product,
                             "folds":record["fold_count"],
                             "net_return":record["net_return"],
                             "benchmark":record["fold_rebalanced_buy_hold_return"]})
    return {"status":"LEDGER_VERIFIED","experiments":verified,
            "promotion_authorized":False}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path)
    parser.add_argument("--db",type=Path,required=True)
    parser.add_argument("--audit-only",action="store_true")
    args=parser.parse_args(argv)
    if args.audit_only:
        result=audit_db(args.db)
    else:
        if args.report is None:
            parser.error("--report is required unless --audit-only")
        payload=json.loads(args.report.read_text(encoding="utf-8"))
        result=store_report(args.db,payload)
        audit_db(args.db)
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
