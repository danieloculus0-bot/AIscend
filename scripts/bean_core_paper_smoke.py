"""Offline integration test of the ACTUAL separately checked-out BEAN SpongeTD.

Only disposable synthetic fixture data is used here to prove integration,
replay idempotence, Q-value changes, and tampering detection.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from src.bean_core_paper_learning import learn_from_paper
from src.bean_five_minute import BAR
from src.forward_paper import _db, freeze


def run(core_dir):
    with tempfile.TemporaryDirectory() as folder:
        paper = Path(folder) / "paper.sqlite3"
        core_db = Path(folder) / "bean-core.sqlite3"
        with closing(sqlite3.connect(paper)) as conn:
            _db(conn)
            for i in range(30):
                product = "BTC-USD"
                policy = "trend"
                now = float((1000 + i * 90) * BAR + 1)
                sha = freeze(conn, product=product, policy=policy, now=now,
                             fee_bps=60, spread_bps=10)
                signal_ts = (999 + i * 90) * BAR
                entry_ts = signal_ts + 2 * BAR
                exit_ts = entry_ts + 72 * BAR
                entry = 100.0
                sell = 101.0 if i % 3 else 98.0
                net = (sell / entry) * (1 - 0.0065) ** 2 - 1
                conn.execute(
                    "INSERT INTO paper_decisions (product,policy_sha,signal_ts,"
                    "observed_at,entry_ts,exit_ts,state,entry_price,exit_price,"
                    "net_return,reason) VALUES(?,?,?,?,? ,?,'SCORED',?,?,?,?)",
                    (product, sha, signal_ts, now, entry_ts, exit_ts,
                     entry, sell, net, "synthetic_fixture_only"))
            conn.commit()
        first = learn_from_paper(
            core_dir=core_dir, paper_db=paper, learning_db=core_db)
        assert first["available_scored_paper_trades"] == 30, first
        assert first["q_updates"] == 30 and first["q_states"] >= 1, first
        assert first["durable_checkpoint"] is True, first
        assert first["durable_q_updates"] == 30, first
        assert not first["improvement_independently_verified"]
        assert first["execution_authority"] == "none"
        second = learn_from_paper(
            core_dir=core_dir, paper_db=paper, learning_db=core_db)
        assert second["q_updates"] == 30, second
        assert second["new_feedback_updates"] == 0, second
        # Prospective shadow choice: Q-values must be used BEFORE a future
        # hypothetical entry, and the choice must survive in the paper ledger.
        import time
        signal=(int(time.time()//BAR)-1)*BAR
        observed=signal+BAR+1
        entry_ts=signal+2*BAR
        exit_ts=entry_ts+72*BAR
        with closing(sqlite3.connect(paper)) as db:
            sha=db.execute("SELECT policy_sha FROM frozen_policy "
                           "WHERE product='BTC-USD'").fetchone()[0]
            db.execute(
                "INSERT INTO paper_decisions (product,policy_sha,signal_ts,"
                "observed_at,entry_ts,exit_ts,state) "
                "VALUES(?,?,?,?,?,?,'PENDING')",
                ("BTC-USD",sha,signal,observed,entry_ts,exit_ts))
            db.commit()
        shadow=learn_from_paper(
            core_dir=core_dir,paper_db=paper,learning_db=core_db,
            clock=lambda:observed+1)
        assert shadow["shadow_choices_committed_before_entry"]==1,shadow
        with closing(sqlite3.connect(paper)) as db:
            proof=db.execute(
                "SELECT action,observed_at FROM bean_paper_shadow "
                "WHERE signal_ts=?",(signal,)).fetchone()
            assert proof and proof[0] in ("trade","cash")
            assert observed < proof[1] < entry_ts,proof
            net=(101.0/100.0)*(1-0.0065)**2-1
            db.execute(
                "UPDATE paper_decisions SET state='SCORED',entry_price=100,"
                "exit_price=101,net_return=? WHERE signal_ts=?",(net,signal))
            db.commit()
        after=learn_from_paper(
            core_dir=core_dir,paper_db=paper,learning_db=core_db,
            clock=lambda:exit_ts+BAR+1)
        assert after["available_scored_paper_trades"]==31,after
        assert after["q_updates"]==31 and after["durable_q_updates"]==30,after
        with closing(sqlite3.connect(paper)) as db:
            db.execute(
                "UPDATE paper_decisions SET entry_price=110,"
                "net_return=(exit_price/110.0)*(1-0.0065)*(1-0.0065)-1 "
                "WHERE signal_ts=(SELECT MIN(signal_ts) FROM paper_decisions)")
            db.commit()
        try:
            learn_from_paper(core_dir=core_dir, paper_db=paper, learning_db=core_db)
        except Exception:
            pass
        else:
            raise AssertionError("Tampered historical paper result was accepted")
        print("Real BEAN SpongeTD trained from 30 synthetic fixtures, "
              "persisted Q checkpoint, committed real prospective shadow paper "
              "choice before entry, scored its observed outcome, idempotent "
              "restart, altered ledger refused")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, required=True)
    run(parser.parse_args().core)
