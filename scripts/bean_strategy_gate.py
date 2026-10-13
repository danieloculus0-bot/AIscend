"""Fail-closed, research-only gate for independent Coinbase strategy trials.

The 30d/60d overlapping reports from one download are diagnostics, never
independent confirmations. No strategy is activated, merged or traded here.
The source/provenance fields are host attestations, not authenticated Coinbase
signatures. A passing historical gate still requires fresh forward paper tests.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


MIN_FRICTION_PER_SIDE = 0.006


def _reject(reason: str) -> dict:
    return {"accepted": False, "reason": reason}


def _interval(report: dict, name: str) -> tuple[int, int] | None:
    value = report.get(name)
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(t) is not int for t in value)):
        return None
    start, end = value
    if start < 0 or end <= start or start % 3600 or end % 3600:
        return None
    return start, end


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def judge(reports: list[dict]) -> dict:
    if len(reports) < 2:
        return _reject("Need at least two disjoint independently selected evaluations")
    if any(not isinstance(r, dict) or
           r.get("historical_source") != "Coinbase public candles" for r in reports):
        return _reject("Missing or unverified Coinbase source declaration")

    strategies = [r.get("strategy") for r in reports]
    if any(not isinstance(s, dict) or not s for s in strategies):
        return _reject("Missing preselected strategy")
    if len({json.dumps(s, sort_keys=True) for s in strategies}) != 1:
        return _reject("Different strategies selected; no stable training-only champion")
    if strategies[0].get("rule") in ("cash", "buy_hold"):
        return _reject("No independent active strategy demonstrated")

    intervals = []
    starting_bankroll = None
    seen_hashes = set()
    for report in reports:
        data = _interval(report, "data_interval")
        train = _interval(report, "training_interval")
        holdout = _interval(report, "holdout_interval")
        if not all((data, train, holdout)):
            return _reject("Missing valid chronological data/train/holdout provenance")
        if not (data[0] == train[0] < train[1] == holdout[0]
                < holdout[1] == data[1]):
            return _reject("Training and holdout intervals are not a sealed chronological split")
        if (report.get("training_bars") != (train[1] - train[0]) // 3600
                or report.get("holdout_bars") != (holdout[1] - holdout[0]) // 3600):
            return _reject("Interval lengths disagree with reported candle counts")
        if report.get("selection_scope") != "training_only" or report.get("holdout_evaluations") != 1:
            return _reject("Holdout not demonstrably sealed from candidate selection")
        digest = report.get("data_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return _reject("Missing dataset fingerprint")
        if digest in seen_hashes:
            return _reject("Duplicate dataset fingerprint")
        seen_hashes.add(digest)
        intervals.append(data)

        friction = _number(report.get("friction_per_side"))
        if friction is None or friction < MIN_FRICTION_PER_SIDE:
            return _reject("Missing or insufficient per-side fee/slippage stress")
        bankroll = _number(report.get("starting_bankroll"))
        if bankroll is None or bankroll <= 0:
            return _reject("Invalid starting bankroll")
        if starting_bankroll is None:
            starting_bankroll = bankroll
        elif not math.isclose(bankroll, starting_bankroll, rel_tol=0, abs_tol=1e-6):
            return _reject("Inconsistent starting bankrolls")

        challenger = report.get("holdout")
        btc = report.get("buy_hold_btc")
        cash = report.get("cash")
        if any(not isinstance(x, dict) for x in (challenger, btc, cash)):
            return _reject("Missing independently evaluated cash/BTC benchmarks")
        final = _number(challenger.get("final"))
        btc_final = _number(btc.get("final"))
        cash_final = _number(cash.get("final"))
        dd = _number(challenger.get("max_drawdown"))
        trades = challenger.get("trades")
        if (any(x is None or x <= 0 for x in (final, btc_final, cash_final))
                or dd is None or not 0 <= dd <= 1
                or type(trades) is not int or trades < 1):
            return _reject("Incomplete or invalid fee-aware benchmark measurements")
        if not math.isclose(cash_final, bankroll, rel_tol=0, abs_tol=1e-5):
            return _reject("Cash benchmark disagrees with starting bankroll")
        if not (final > cash_final and final > btc_final):
            return _reject("Did not beat both cash and BTC after costs")
        if (report.get("beats_btc_holdout") is not True
                or report.get("beats_cash_holdout") is not True):
            return _reject("Benchmark verdict disagrees with reported values")
        if dd > 0.25:
            return _reject("Excessive observed drawdown")

    ordered = sorted(intervals)
    if any(previous[1] > current[0] for previous, current in zip(ordered, ordered[1:])):
        return _reject("Overlapping source windows are not independent holdouts")
    return {"accepted": True,
            "reason": "Disjoint fee-aware historical trials passed; forward paper still required",
            "candidate": strategies[0],
            "evidence_windows": len(ordered),
            "research_only": True}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("reports", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=Path("artifacts/bean-promotion-gate.json"))
    a = p.parse_args()
    verdict = judge([json.loads(f.read_text(encoding="utf-8")) for f in a.reports])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    # Rejection is a valid research outcome, not a CI failure.


if __name__ == "__main__":
    main()
