"""Fail-closed BEAN gate for independent, fee-aware Coinbase holdout reports.

Reports alone cannot authenticate exchange data or guarantee future profits.
No execution, deployment, or holdout-driven parameter selection occurs here.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path


def judge(reports: list[dict]) -> dict:
    if len(reports) < 2:
        return {"accepted": False, "reason": "Need multiple independent market windows"}
    if any(x.get("historical_source") != "Coinbase public candles" for x in reports):
        return {"accepted": False, "reason": "Unverified or synthetic source"}
    strategies = [x.get("strategy") for x in reports]
    if any(not isinstance(s, dict) for s in strategies) or len({json.dumps(s, sort_keys=True) for s in strategies}) != 1:
        return {"accepted": False, "reason": "Different or missing strategies: no stable champion"}

    intervals, fees = [], []
    for report in reports:
        try:
            window = report["holdout_window"]
            start_ts, end_ts = int(window["start_ts"]), int(window["end_ts"])
            train_end = int(report["training_end_ts"])
            fee = float(report["cost_per_side"])
            initial = float(report["starting_bankroll"])
            final = float(report["holdout"]["final"])
            btc = float(report["buy_hold_btc"]["final"])
            cash = float(report["cash"]["final"])
            drawdown = float(report["holdout"]["max_drawdown"])
            trades = int(report["holdout"]["trades"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"accepted": False, "reason": "Missing or invalid holdout provenance or fee-aware benchmark"}
        if not (train_end < start_ts <= end_ts):
            return {"accepted": False, "reason": "Invalid chronological train/holdout boundary"}
        if not (all(math.isfinite(v) for v in (fee, initial, final, btc, cash, drawdown))
                and 0 < fee < 1 and initial > 0 and btc > 0 and cash > 0
                and 0 <= drawdown <= 0.25 and trades >= 1):
            return {"accepted": False, "reason": "Invalid costs, drawdown, trade count or benchmark values"}
        if not (report.get("beats_btc_holdout") is True
                and report.get("beats_cash_holdout") is True
                and final > max(initial, btc, cash)):
            return {"accepted": False, "reason": "Failed fee-aware cash/BTC benchmark or positive-return requirement"}
        intervals.append((start_ts, end_ts))
        fees.append(fee)

    intervals.sort()
    if any(right[0] <= left[1] for left, right in zip(intervals, intervals[1:])):
        return {"accepted": False, "reason": "Holdout windows overlap; not independent evidence"}
    if max(fees) - min(fees) > 1e-12:
        return {"accepted": False, "reason": "Inconsistent fee assumptions between windows"}
    return {"accepted": True, "reason": "Historical gate passed; independent forward paper test still required",
            "candidate": strategies[0]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("reports", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=Path("artifacts/bean-promotion-gate.json"))
    a = p.parse_args()
    verdict = judge([json.loads(f.read_text(encoding="utf-8")) for f in a.reports])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    # A rejected strategy is a valid finding; fail only on corrupt processing.


if __name__ == "__main__":
    main()
