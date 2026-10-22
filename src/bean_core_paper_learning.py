"""Run the REAL BEAN Core SpongeTD on causal, scored Coinbase paper outcomes.

This is retrospective learning from previously frozen *paper* executions.
It does not certify a strategy, authenticate a Coinbase account or execute a
trade. The separate BEAN source checkout is mandatory: no legacy fallback.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from .bean_core_bridge import core_identity
from .bean_five_minute import BAR
from .forward_paper import HORIZONS, policy_identity

SCHEMA = "aiscend.bean-core-paper-learning.v1"


def paper_receipts(path):
    """Return immutable-looking paper receipts, rejecting legacy timing leaks.

    SQLite is host-supplied, not an independent Coinbase attestation. Never
    confuse these receipts with authenticated venue fills.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError("Paper evidence database does not exist")
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        cols = [row[1] for row in db.execute("PRAGMA table_info(paper_decisions)")]
        if "entry_ts" not in cols:
            raise ValueError("Legacy paper results contain invalid entry timing")
        rows = db.execute(
            """SELECT p.product,p.policy_sha,p.signal_ts,p.observed_at,
                      p.entry_ts,p.exit_ts,p.entry_price,p.exit_price,p.net_return,
                      f.policy,f.fee_bps,f.spread_bps
               FROM paper_decisions p JOIN frozen_policy f ON
                    f.product=p.product AND f.policy_sha=p.policy_sha
               WHERE p.state='SCORED'
               ORDER BY p.exit_ts,p.product,p.signal_ts"""
        ).fetchall()
    result = []
    for x in rows:
        item = dict(x)
        p, policy = item["product"], item["policy"]
        if (item["policy_sha"] != policy_identity(p, policy) or
                item["entry_ts"] != item["signal_ts"] + 2 * BAR or
                item["exit_ts"] != item["entry_ts"] + HORIZONS[policy] * BAR or
                not item["observed_at"] < item["entry_ts"]):
            raise ValueError("Paper evidence has inconsistent causal timestamps")
        entry, exit_price, earned = (float(item[k]) for k in
                                     ("entry_price", "exit_price", "net_return"))
        fee, spread = float(item["fee_bps"]), float(item["spread_bps"])
        if not (all(math.isfinite(v) for v in (entry, exit_price, earned, fee, spread))
                and entry > 0 and exit_price > 0
                and 0 <= fee <= 1000 and 0 <= spread <= 1000):
            raise ValueError("Invalid costed paper fill")
        modeled = (exit_price / entry) * (
            1 - (fee + spread / 2) / 10000) ** 2 - 1
        if abs(modeled - earned) > 1e-7:
            raise ValueError("Paper outcome does not match saved execution costs")
        result.append({
            "product": p, "policy": policy,
            "signal_ts": int(item["signal_ts"]),
            "entry_ts": int(item["entry_ts"]), "exit_ts": int(item["exit_ts"]),
            "net_return": round(earned, 10), "fee_bps": fee,
            "spread_bps": spread, "policy_sha": item["policy_sha"],
        })
    return result


def _digest(rows):
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"),
                   allow_nan=False).encode("utf-8")).hexdigest()


# Executed only in a dedicated Python process with the *actual* BEAN repo
# installed on PYTHONPATH. No exchange credentials or order APIs are exposed.
_BEAN_WORKER = r"""
import hashlib,json,sys
from bean.learning.runtime import LearningRuntime
from bean.optimization.sponge_td import OBJECTIVE
payload=json.load(sys.stdin)
records=payload["records"]
service=LearningRuntime(payload["db_path"],actions=("trade","cash"),seed=17)
try:
    prior=service.policy.steps
    history=service.policy.journal.events(OBJECTIVE)
    if history:
        quality=history[-1]["quality"]
        actual=hashlib.sha256(json.dumps(records[:prior],sort_keys=True,
            separators=(",",":"),allow_nan=False).encode()).hexdigest()
        if (prior>len(records) or quality["receipt_sha256"]!=actual):
            raise ValueError("Previously checkpointed paper evidence changed")
    elif prior:
        raise ValueError("Missing original checkpoint evidence")
    learned=0
    choices={"trade":0,"cash":0}
    for i,item in enumerate(records[prior:],start=prior):
        choice=service.choose(
            episode_id="paper-"+str(i),domain="aiscend-paper",
            state={"product":item["product"],"policy":item["policy"]},
            location_ref={"product":item["product"],"policy":item["policy"]},
            legal_actions=["trade","cash"])
        action=choice["action"]
        reward=10000.0*item["net_return"] if action=="trade" else 0.0
        service.feedback(episode_id="paper-"+str(i),reward=reward,done=True)
        choices[action]+=1
        learned+=1
    checkpoint=None
    if len(records)>=30 and service.policy.steps-(history[-1]["steps"] if history else 0)>=30:
        digest=hashlib.sha256(json.dumps(records[:service.policy.steps],
            sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        # 0 vs 0 is deliberately non-promotable. A separate, blind
        # forward evaluation must establish real performance improvement.
        checkpoint=service.checkpoint(
            baseline=0.0,candidate=0.0,samples=service.policy.steps,
            evaluation_sha256=digest)
    result={
        "engine": "BEAN Core LearningRuntime/SpongeTD",
        "q_updates":service.policy.steps,
        "q_states":service.status()["q_states"],
        "new_feedback_updates":learned,
        "replayed_after_checkpoint":learned,
        "available_scored_paper_trades":len(records),
        "decisions_this_run":choices,
        "checkpoint_saved_this_run":checkpoint is not None,
        "durable_checkpoint":bool(service.status()["checkpoints"]),
        "durable_q_updates":service.policy.journal.events(OBJECTIVE)[-1]["steps"]
                           if service.status()["checkpoints"] else 0,
        "policy_trading_authorized":False,
        "improvement_independently_verified":False,
        "feedback_origin":"timestamped_fee_net_paper_receipts_not_actual_fills",
        "learning_mode":"retrospective_replay_not_forward_selection",
    }
    print(json.dumps(result,sort_keys=True))
finally:
    service.close()
"""


def learn_from_paper(*, core_dir, paper_db, learning_db, timeout=60):
    """Learn from scored paper observations using the real external BEAN Core."""
    identity = core_identity(core_dir)
    receipts = paper_receipts(paper_db)
    learning_db = Path(learning_db).resolve()
    if learning_db == Path(paper_db).resolve():
        raise ValueError("BEAN Q-memory must have a separate SQLite journal")
    learning_db.parent.mkdir(parents=True, exist_ok=True)
    payload = {"records": receipts, "db_path": str(learning_db)}
    env = {"PYTHONPATH": str(Path(core_dir).resolve()),
           "PATH": os.environ.get("PATH", ""),
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", _BEAN_WORKER],
        input=json.dumps(payload, sort_keys=True, allow_nan=False),
        capture_output=True, text=True, env=env, timeout=timeout, check=True)
    output = json.loads(result.stdout)
    return {
        "schema": SCHEMA, "core_sha": identity["sha"],
        "core_source": identity["source"],
        "receipt_digest": _digest(receipts),
        "execution_authority": "none", "paper_only": True,
        **output,
    }
