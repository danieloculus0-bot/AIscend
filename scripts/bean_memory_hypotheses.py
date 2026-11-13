"""Causal, fee-net offline learning hypotheses against retained BEAN fold ledgers.

Strict historical research. Each strategy sees only prior finished fold outcomes
when selecting the next fold's candidate; the target fold is scored afterward.
Unlike a passing unit test, the all-market report explicitly compares profits
with no-memory selection. No Coinbase credentials or live order functions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from contextlib import closing
from statistics import median

from scripts.bean_five_year_ledger import audit_db

POLICIES = ("no_memory", "bean_existing", "recency_ewma",
            "trend_breadth_gate", "same_rule_confirmation")


def _pick(scores):
    eligible = [(float(v), -i, rule)
                for i, (rule, v) in enumerate(scores.items())
                if v is not None and math.isfinite(v) and v > 0]
    return max(eligible)[2] if eligible else None


def replay_market(report):
    """All candidate trade returns read only AFTER selections are locked."""
    capital = {name: 1. for name in POLICIES}
    active = {name: 0 for name in POLICIES}
    diverged = {name: 0 for name in POLICIES}
    earlier = []
    recorded = []
    for fold in report["folds"]:
        training = fold["baseline_training_scores"]
        completed_rules = fold["all_rule_test_results_after_close"]
        if set(training) != set(completed_rules):
            raise ValueError("HYPOTHESIS_STRATEGY_SET_MISMATCH")
        baseline = _pick(training)
        original = (str(tuple(fold["winner"]))
                    if isinstance(fold["winner"], list) else None)
        # EWMA of ONLY earlier completed fold outcomes, never target fold.
        moving = {rule: 0. for rule in training}
        for prev in earlier:
            for rule in training:
                previous_reward = (prev[rule]["net_return"] -
                                   .4 * prev[rule]["max_drawdown"])
                moving[rule] = .65 * moving[rule] + .35 * previous_reward
        ewma = _pick({r: v + moving[r] if v is not None else None
                      for r, v in training.items()})
        breadth = None
        if len(earlier) >= 2:
            breadth = sum(sum(
                x[r]["net_return"] for r in training) / len(training)
                for x in earlier[-2:]) / 2.
        trend_gate = baseline if breadth is not None and breadth > 0 else None
        same_rule = (baseline if baseline and len(earlier) >= 2 and all(
            x[baseline]["net_return"] > 0 for x in earlier[-2:]) else None)
        decisions = {"no_memory": baseline, "bean_existing": original,
                     "recency_ewma": ewma, "trend_breadth_gate": trend_gate,
                     "same_rule_confirmation": same_rule}
        result = {}
        for mode, rule in decisions.items():
            fee_net = completed_rules[rule]["net_return"] if rule else 0.
            if rule: active[mode] += 1
            if rule != baseline: diverged[mode] += 1
            capital[mode] *= 1. + fee_net
            result[mode] = {"rule": rule, "net_return": fee_net}
        recorded.append({"test_start": fold["test_start"],
                         "test_end": fold["test_end"], "strategies": result})
        earlier.append(completed_rules)
    returns = {m: round(v - 1., 8) for m, v in capital.items()}
    if (abs(returns["no_memory"] - report["no_memory_net_return"]) > 1e-6
            or abs(returns["bean_existing"] - report["net_return"]) > 1e-6):
        raise ValueError("CONTROL_REPLAY_MISMATCH")
    return {"final_returns": returns, "active_folds": active,
            "changed_choice_folds": diverged, "fold_results": recorded}


def evaluate_archived_db(db_path, output=None):
    audited = audit_db(db_path)  # immutable event/hash audit before every read
    with closing(sqlite3.connect(db_path)) as db:
        rows = db.execute(
            "SELECT product,report FROM experiments ORDER BY product").fetchall()
    markets = {}
    for product, encoded in rows:
        report = json.loads(encoded)
        if report["status"] != "HISTORICAL_WALKFORWARD_RESEARCH":
            continue
        result = replay_market(report)
        result["buy_hold"] = report["continuous_buy_hold_return"]
        markets[product] = result
    if len(markets) != len(audited["experiments"]):
        raise ValueError("ARCHIVED_EXPERIMENT_COUNT_MISMATCH")
    summary = {}
    for mode in POLICIES:
        values = [r["final_returns"][mode] for r in markets.values()]
        summary[mode] = {
            "markets": len(values),
            "profitable_after_costs": sum(x > 0 for x in values),
            "profitable_and_above_buy_hold": sum(
                r["final_returns"][mode] > max(0., r["buy_hold"])
                for r in markets.values()),
            "median_net_return": median(values) if values else None,
            "improved_vs_no_memory": sum(
                r["final_returns"][mode] > r["final_returns"]["no_memory"]
                for r in markets.values()),
            "worse_vs_no_memory": sum(
                r["final_returns"][mode] < r["final_returns"]["no_memory"]
                for r in markets.values()),
            "changed_choice_folds": sum(
                r["changed_choice_folds"][mode] for r in markets.values()),
        }
    report = {
        "schema": "aiscend.bean.offline_memory_hypotheses.v1",
        "source_sqlite_sha256": hashlib.sha256(Path(db_path).read_bytes()).hexdigest(),
        "markets": markets, "policy_summary": summary,
        "research_only": True, "promotion_authorized": False,
        "limitations": "Historical causal replay of existing six-hour fold outcomes. "
                       "Policies are not independent unseen-market confirmation; "
                       "does not demonstrate live profitability.",
    }
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, sort_keys=True, indent=2,
                                   allow_nan=False) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    result = evaluate_archived_db(args.db, args.output)
    print(json.dumps(result["policy_summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
