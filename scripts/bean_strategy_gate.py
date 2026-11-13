"""BEAN strategy acceptance gate across independent Coinbase replay reports.

Only reports results. It never activates a strategy or merges GitHub branches.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def judge(reports: list[dict]) -> dict:
    if len(reports) < 2:
        return {"accepted": False, "reason": "Need multiple independent market windows"}
    if any(x.get("historical_source") != "Coinbase public candles" for x in reports):
        return {"accepted": False, "reason": "Unverified or synthetic source"}
    strategies = [x.get("strategy") for x in reports]
    if len({json.dumps(s, sort_keys=True) for s in strategies}) != 1:
        return {"accepted": False, "reason": "Different strategies selected: no stable champion"}
    if any(not x.get("beats_btc_holdout") or not x.get("beats_cash_holdout")
           for x in reports):
        return {"accepted": False, "reason": "Failed independent benchmark"}
    if any(x["holdout"]["max_drawdown"] > 0.25 for x in reports):
        return {"accepted": False, "reason": "Excessive observed drawdown"}
    if any(x["holdout"]["trades"] < 1 for x in reports):
        return {"accepted": False, "reason": "No demonstrated active trading edge"}
    return {"accepted": True, "reason": "Historical gate passed; forward paper test still required",
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
