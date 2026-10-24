"""Causal retrieval of past BEAN trading research from retained SQLite evidence.

Only completed, independently recorded historical experiments ending strictly
BEFORE the first candle of the next challenge can seed the next learner.
Never reuse earlier results as prospective proof on overlapping test periods.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

from scripts.bean_five_year_ledger import audit_db
from src.published_strategy_research import RULES


def recall_prior(db_path, *, product, before_ts):
    """Return deterministic policy priors and source SHA receipts, or no priors.

    Entire source experiment must end before the destination test starts,
    even if it contains an earlier fold. No lookahead across overlapping
    replays. A duplicate report contributes once due to immutable SHA IDs.
    """
    result = {"status": "NO_ELIGIBLE_PRIOR", "product": product,
              "before_ts": int(before_ts), "source_experiments": [],
              "prior_scores": {}, "promotion_authorized": False}
    if db_path is None or not Path(db_path).is_file():
        return result
    audit_db(db_path)  # fail closed on altered events or outcomes
    buckets = defaultdict(list)
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("PRAGMA query_only=ON")
        records = db.execute(
            "SELECT experiment_sha,end_ts,report FROM experiments "
            "WHERE product=? AND end_ts < ? ORDER BY end_ts,experiment_sha",
            (product, int(before_ts))).fetchall()
    for sha, end_ts, raw in records:
        report = json.loads(raw)
        # End timestamp is the experiment's stated history end, not just its
        # last selected fold. This intentionally excludes overlapping replays.
        if report.get("product") != product:
            raise ValueError("RECALL_PRODUCT_MISMATCH")
        latest = [e for e in report["ledger"]
                  if e["type"] == "bean_feedback_postclose"]
        if not latest:
            continue
        event = latest[-1]
        if event["observed_through"] >= before_ts:
            raise ValueError("RECALL_FUTURE_EVIDENCE")
        values = event["q_after"]
        accepted = 0
        for name, score in values.items():
            try:
                rule = ast.literal_eval(name)
            except (SyntaxError, ValueError):
                continue
            if rule not in RULES or not isinstance(score, (int, float)) or not math.isfinite(score):
                continue
            buckets[rule].append(float(score))
            accepted += 1
        if accepted:
            result["source_experiments"].append(
                {"experiment_sha": sha, "observed_through": event["observed_through"],
                 "end_ts": end_ts, "folds": len(latest)})
    if not result["source_experiments"]:
        return result
    # Cross-experiment mean, not sum: rerunning the same historical segment
    # cannot inflate a policy merely by adding more memory copies.
    result["prior_scores"] = {repr(rule): round(sum(x)/len(x), 8)
                              for rule, x in sorted(buckets.items(),
                                                   key=lambda kv: RULES.index(kv[0]))}
    result["status"] = "RECALLED_CAUSAL_EVIDENCE"
    return result


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--product", required=True)
    p.add_argument("--before-ts", type=int, required=True)
    args = p.parse_args(argv)
    print(json.dumps(recall_prior(args.db, product=args.product,
                                  before_ts=args.before_ts), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
