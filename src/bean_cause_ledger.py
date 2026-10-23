"""Compact, append-only-by-contract root-cause index for BEAN paper decisions.

This is a diagnostic attribution, NOT proof of economic causation. An
after-exit label must never be passed to BEAN as a pre-entry observation.
The original source data, decision trace and model checkpoints stay intact.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import date
import csv
import hashlib
import json
import math
from pathlib import Path
import sqlite3

SCHEMA = "aiscend.bean.root-cause-ledger.v1"
CODES = {
    "01": ("BAD_MARKET_MOVE", "bad", "Trade lost on underlying move before costs"),
    "02": ("GOOD_NET_TRADE", "good", "Trade gained after estimated spread and fees"),
    "03": ("BAD_COST_DRAG", "bad", "Positive gross trade became non-positive after costs"),
    "04": ("GOOD_CASH_AVOIDED_LOSS", "good", "Cash avoided a negative hypothetical trade"),
    "05": ("BAD_CASH_MISSED_GAIN", "bad", "Cash missed a positive hypothetical trade"),
    "06": ("NEUTRAL_NO_EDGE", "neutral", "No meaningful signed fee-net edge"),
}
FEATURES = ("momentum_7d", "momentum_28d", "momentum_84d", "volatility_14d")
COLUMNS = (
    "seq","episode","product","decision_date","entry_date","exit_date",
    "action","untrained_action","code","verdict","momentum_7d",
    "momentum_28d","momentum_84d","volatility_14d","entry_open",
    "exit_open","gross_bps","net_bps","cost_bps","regret_bps",
    "pre_sleeve_usd","post_sleeve_usd","trade_qty","buy_fee_usd",
    "sell_fee_usd","pnl_usd","q_trade_before","q_cash_before",
    "q_trade_after","q_cash_after","updates_before","updates_after",
    "source_decision_sha256","source_outcome_sha256","prior_receipt_sha",
    "receipt_sha256",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def hexdigest(data):
    return hashlib.sha256(data).hexdigest()


def classify(action, *, gross_bps, net_bps, flat_tolerance=0.000001):
    if action not in ("cash", "trade") or not all(
            math.isfinite(x) for x in (gross_bps, net_bps)):
        raise ValueError("Bad action or price return")
    if abs(net_bps) <= flat_tolerance:
        return "06"
    if action == "cash":
        return "04" if net_bps < 0 else "05"
    if net_bps > 0:
        return "02"
    return "03" if gross_bps > 0 else "01"


def db_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cause_codes (
        code TEXT PRIMARY KEY, label TEXT NOT NULL,
        verdict TEXT NOT NULL, definition TEXT NOT NULL)""")
    conn.executemany(
        "INSERT OR IGNORE INTO cause_codes VALUES(?,?,?,?)",
        [(k, *v) for k, v in CODES.items()])
    conn.execute("""CREATE TABLE IF NOT EXISTS decisions (
        seq INTEGER PRIMARY KEY, episode TEXT NOT NULL UNIQUE,
        product TEXT NOT NULL, decision_date TEXT NOT NULL,
        entry_date TEXT NOT NULL, exit_date TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('trade','cash')),
        untrained_action TEXT NOT NULL,
        code TEXT NOT NULL, verdict TEXT NOT NULL,
        momentum_7d TEXT NOT NULL, momentum_28d TEXT NOT NULL,
        momentum_84d TEXT NOT NULL, volatility_14d TEXT NOT NULL,
        entry_open REAL NOT NULL, exit_open REAL NOT NULL,
        gross_bps REAL NOT NULL, net_bps REAL NOT NULL,
        cost_bps REAL NOT NULL, regret_bps REAL NOT NULL,
        pre_sleeve_usd REAL NOT NULL, post_sleeve_usd REAL NOT NULL,
        trade_qty REAL NOT NULL, buy_fee_usd REAL NOT NULL,
        sell_fee_usd REAL NOT NULL, pnl_usd REAL NOT NULL,
        q_trade_before REAL NOT NULL, q_cash_before REAL NOT NULL,
        q_trade_after REAL NOT NULL, q_cash_after REAL NOT NULL,
        updates_before INTEGER NOT NULL, updates_after INTEGER NOT NULL,
        source_decision_sha256 TEXT NOT NULL,
        source_outcome_sha256 TEXT NOT NULL,
        prior_receipt_sha TEXT NOT NULL,
        receipt_sha256 TEXT NOT NULL UNIQUE,
        FOREIGN KEY(code) REFERENCES cause_codes(code))""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_decision_lookup ON decisions"
        "(product,exit_date,momentum_7d,momentum_28d,momentum_84d,volatility_14d)")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS refuse_mutated_decision
        BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT,'append-only decision'); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS refuse_removed_decision
        BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT,'append-only decision'); END""")


def validate_source(source, *, expected_digest=None):
    """Original JSONL stays unchanged; verify individual source hashes."""
    lines = Path(source).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(s) for s in lines if s.strip()]
    if not rows:
        raise ValueError("No historical blind decisions")
    for row in rows:
        # The historical collector stores decision and outcome hashes over
        # distinct views of the same record. Preserve those exact boundaries.
        outcome = dict(row)
        got_outcome = outcome.pop("outcome_sha256", None)
        if hexdigest(canonical(outcome)) != got_outcome:
            raise ValueError("Corrupted historical outcome: " + str(row.get("episode")))
        decision = {k: row[k] for k in (
            "episode","product","decision_time_utc","last_feature_date",
            "entry_date","exit_date","state_from_past_only",
            "bean_action_pre_entry","untrained_action_pre_entry",
            "q_before","updates_before","prior_sha256")}
        if hexdigest(canonical(decision)) != row["decision_sha256"]:
            raise ValueError("Corrupted historical decision commitment")
        if (not row["last_feature_date"] < row["entry_date"] < row["exit_date"]
                or row["decision_time_utc"][:10] >= row["entry_date"]
                or row["product"] != row["state_from_past_only"]["symbol"]):
            raise ValueError("Noncausal timestamps in historical decision")
        if any(k not in row["state_from_past_only"] for k in FEATURES):
            raise ValueError("Missing prior-only market state")
    # A saved tip refers to the last recorded outcome, not a wallet fill.
    if expected_digest and rows[-1]["outcome_sha256"] != expected_digest:
        raise ValueError("Source evidence ledger tip changed")
    return rows


def to_compact(rows, *, fee_bps=60.0, spread_bps=10.0):
    """Derive inspectable hypothetical trades, cash choices, and dollar P&L."""
    if not (0 <= fee_bps < 1000 and 0 <= spread_bps < 1000):
        raise ValueError("Invalid fee/spread")
    one_side = (fee_bps + spread_bps / 2) / 10000
    principal = {}
    tip = "GENESIS"
    out = []
    for i, r in enumerate(rows, 1):
        p = r["product"]
        start = principal.get(p, 500.0)
        entry = float(r["simulated_entry_open"])
        close = float(r["simulated_exit_open"])
        net = float(r["hypothetical_trade_net_return"])
        gross = close / entry - 1
        if not all(map(math.isfinite, (entry, close, net, gross))) or entry <= 0 or close <= 0:
            raise ValueError("Bad source prices")
        reconstructed = close / entry * (1 - one_side) ** 2 - 1
        if abs(reconstructed - net) > 2e-10:
            raise ValueError("Recorded paper reward does not reconcile to fees")
        action = r["bean_action_pre_entry"]
        after = start * (1 + net) if action == "trade" else start
        recorded = float(r["bean_equity_sleeve_usd"])
        if abs(after - recorded) > 0.0001:
            raise ValueError("Simulation equity mismatches saved decision ledger")
        qty = start * (1 - one_side) / entry if action == "trade" else 0.0
        buy_fee = start * fee_bps / 10000 if action == "trade" else 0.0
        sell_fee = qty * close * fee_bps / 10000 if action == "trade" else 0.0
        code = classify(action, gross_bps=gross * 10000, net_bps=net * 10000)
        regret = max(0.0, net * 10000) if action == "cash" else max(0.0, -net * 10000)
        record = {
            "seq": i,"episode":r["episode"],"product":p,
            "decision_date":r["last_feature_date"],
            "entry_date":r["entry_date"],"exit_date":r["exit_date"],
            "action":action,"untrained_action":r["untrained_action_pre_entry"],
            "code":code,"verdict":CODES[code][1],
            **{k:r["state_from_past_only"][k] for k in FEATURES},
            "entry_open":entry,"exit_open":close,"gross_bps":gross*10000,
            "net_bps":net*10000,
            "cost_bps":(gross-net)*10000,
            "regret_bps":regret,
            "pre_sleeve_usd":start,"post_sleeve_usd":after,
            "trade_qty":qty,"buy_fee_usd":buy_fee,"sell_fee_usd":sell_fee,
            "pnl_usd":after-start,
            "q_trade_before":float(r["q_before"][0]),
            "q_cash_before":float(r["q_before"][1]),
            "q_trade_after":float(r["q_after"][0]),
            "q_cash_after":float(r["q_after"][1]),
            "updates_before":int(r["updates_before"]),
            "updates_after":int(r["updates_after"]),
            "source_decision_sha256":r["decision_sha256"],
            "source_outcome_sha256":r["outcome_sha256"],
            "prior_receipt_sha":tip,
        }
        record["receipt_sha256"] = hexdigest(canonical(record))
        tip = record["receipt_sha256"]
        principal[p] = after
        out.append(record)
    return out


def write_index(source, *, database, csv_path, codebook_path=None,
                original_summary=None):
    summary = json.loads(Path(original_summary).read_text()) if original_summary else {}
    rows = validate_source(source, expected_digest=summary.get("event_tip_sha256"))
    compact = to_compact(rows, fee_bps=summary.get("fee_bps_each_side",60),
                         spread_bps=summary.get("spread_bps",10))
    if summary:
        if summary["opportunities"] != len(compact):
            raise ValueError("Decision count mismatch")
        final = sum(c["post_sleeve_usd"] for c in compact[-2:])
        if abs(final - summary["final_equity_usd"]["bean"]) > 0.0001:
            raise ValueError("Final simulated capital mismatch")
    database, csv_path = Path(database), Path(csv_path)
    if database.exists() or csv_path.exists():
        raise FileExistsError("Root cause history is immutable; use new filenames")
    database.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db:
        db_schema(db)
        db.executemany(
            "INSERT INTO decisions (" + ",".join(COLUMNS) + ") VALUES(" +
            ",".join("?" for _ in COLUMNS) + ")",
            [tuple(row[k] for k in COLUMNS) for row in compact])
        db.commit()
        if db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] != len(compact):
            raise AssertionError("Ledger lost records")
    with csv_path.open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(compact)
    if codebook_path:
        path=Path(codebook_path)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(
            {"schema": SCHEMA,"labels": [
                {"code":code,"label":c[0],"verdict":c[1],"definition":c[2]}
                for code,c in CODES.items()],
             "disclaimer":"Post-exit diagnostic attribution only, not proven causes."},
            indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return {
        "schema": SCHEMA,"rows":len(compact),"latest_receipt_sha":compact[-1]["receipt_sha256"],
        "root_code_counts":dict(sorted(Counter(x["code"] for x in compact).items())),
        "net_pnl_usd":round(sum(x["pnl_usd"] for x in compact),4),
        "source_outcome_sha":rows[-1]["outcome_sha256"],
        "sqlite":str(database),"csv":str(csv_path),
        "post_outcome_diagnostics_not_causal_proof":True,
    }


def verify_index(path):
    """Read and rehash every compact entry, including its preceding link."""
    with closing(sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro",uri=True)) as db:
        db.row_factory=sqlite3.Row
        records=[dict(x) for x in db.execute(
            "SELECT "+",".join(COLUMNS)+" FROM decisions ORDER BY seq")]
    tip="GENESIS"
    for i,row in enumerate(records,1):
        recorded=row.pop("receipt_sha256")
        if row["seq"]!=i or row["prior_receipt_sha"]!=tip:
            raise ValueError("Broken root-cause receipt sequence")
        if hexdigest(canonical(row)) != recorded:
            raise ValueError("Mutated root-cause ledger record")
        tip=recorded
    return {"rows":len(records),"receipt_tip_sha256":tip}


def rank_prior(database, *, product, state, as_of, min_samples=5, limit=8):
    """Only prior EXITED outcomes; compact and deterministic feature retrieval.

    This function may be used at a pre-entry decision boundary; codes and
    returns of that decision or later ones are never visible.
    """
    as_of=date.fromisoformat(str(as_of))
    if not all(k in state for k in FEATURES) or min_samples<1 or limit<1:
        raise ValueError("Invalid prior-only market query")
    with closing(sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro",uri=True)) as db:
        db.row_factory=sqlite3.Row
        prior=[dict(x) for x in db.execute(
            "SELECT product,exit_date,net_bps,code,"+",".join(FEATURES)+
            " FROM decisions WHERE exit_date<? AND product=? ORDER BY exit_date",
            (as_of.isoformat(),product))]
    matches=[]
    # Broad to specific matching is deliberately bounded; never give BEAN
    # 500 raw old records when 6 aggregate entries are enough.
    for dimensions in [
        ("momentum_7d","momentum_28d","momentum_84d","volatility_14d"),
        ("momentum_7d","momentum_28d","volatility_14d"),
        ("momentum_7d","momentum_28d"),
        ("momentum_28d",),
        ("volatility_14d",),
        tuple(),
    ]:
        data=[r for r in prior if all(r[k]==state[k] for k in dimensions)]
        if len(data)<min_samples:
            continue
        n=len(data)
        mean=sum(r["net_bps"] for r in data)/n
        wins=sum(r["net_bps"]>0 for r in data)
        shrink=n/(n+20)
        codes=dict(sorted(Counter(r["code"] for r in data).items()))
        matches.append({
            "keys":",".join(dimensions) if dimensions else "ALL_PRIOR",
            "samples":n,"win_rate_pct":round(100*wins/n,2),
            "average_trade_net_bps":round(mean,3),
            "rank_score_bps":round(mean*shrink,3),
            "diagnostic_counts":codes,
        })
    matches.sort(key=lambda x:(-x["rank_score_bps"],-x["samples"],x["keys"]))
    return {"product":product,"as_of":as_of.isoformat(),"prior_only":True,
            "indexed_rows":len(prior),"rankings":matches[:limit],
            "past_observations_only":True,
            "does_not_prove_root_causation":True}
