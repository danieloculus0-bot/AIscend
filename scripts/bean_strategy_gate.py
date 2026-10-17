"""BEAN strategy acceptance gate across independent Coinbase replay reports.

Only reports results. It never activates a strategy or merges GitHub branches.
"""
from __future__ import annotations
import argparse
import json
import math
import re
from pathlib import Path


def judge(reports: list[dict]) -> dict:
    """Require distinct, non-overlapping OOS periods for one frozen strategy.

    Source names and dataset hashes are audit aids, not exchange authentication.
    This read-only gate never promotes a strategy to live execution.
    """
    if len(reports) < 2:
        return {"accepted": False, "reason": "Need multiple independent market windows"}
    if any(x.get("historical_source") != "Coinbase public candles" for x in reports):
        return {"accepted": False, "reason": "Unverified or synthetic source"}
    strategies = [x.get("strategy") for x in reports]
    if any(not isinstance(s, dict) or not s for s in strategies):
        return {"accepted": False, "reason": "Missing fixed strategy identity"}
    if len({json.dumps(s, sort_keys=True) for s in strategies}) != 1:
        return {"accepted": False, "reason": "Different strategies selected: no stable champion"}
    required = ("training_end_ts", "holdout_start_ts", "holdout_end_ts",
                "holdout_execution_start_ts", "dataset_sha256",
                "fee_stress_per_side", "execution_model")
    if any(any(field not in x for field in required) for x in reports):
        return {"accepted": False, "reason": "Missing independently auditable OOS provenance"}
    try:
        for x in reports:
            digest = x["dataset_sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                return {"accepted": False, "reason": "Invalid dataset fingerprint"}
            if not (int(x["training_end_ts"]) < int(x["holdout_start_ts"])
                    < int(x["holdout_execution_start_ts"])
                    <= int(x["holdout_end_ts"])):
                return {"accepted": False, "reason": "Invalid chronological split"}
            if (x["execution_model"] != "prior_close_signal_next_open_fee_v1"
                    or not math.isfinite(float(x["fee_stress_per_side"]))
                    or float(x["fee_stress_per_side"]) < 0.006):
                return {"accepted": False, "reason": "Unverified execution or fee stress"}
        if len({x["dataset_sha256"] for x in reports}) != len(reports):
            return {"accepted": False, "reason": "Repeated dataset is not independent"}
        ordered = sorted(reports, key=lambda x: int(x["holdout_start_ts"]))
        if any(int(b["holdout_start_ts"]) <= int(a["holdout_end_ts"])
               for a, b in zip(ordered, ordered[1:])):
            return {"accepted": False, "reason": "Overlapping OOS periods are not independent"}
        if any(int(x["training_end_ts"]) >= int(ordered[0]["holdout_start_ts"])
               for x in reports):
            return {"accepted": False, "reason": "Training touched another OOS period"}
        for x in reports:
            holdout = x["holdout"]
            if not x.get("beats_btc_holdout") or not x.get("beats_cash_holdout"):
                return {"accepted": False, "reason": "Failed independent benchmark"}
            if (float(holdout["net_return"]) <= 0
                    or float(holdout["final"]) <= max(
                        float(x["cash"]["final"]), float(x["buy_hold_btc"]["final"]))):
                return {"accepted": False, "reason": "No positive fee-aware edge over cash and BTC"}
            if (float(holdout["max_drawdown"]) > 0.25
                    or not math.isfinite(float(holdout["max_drawdown"]))):
                return {"accepted": False, "reason": "Excessive or invalid observed drawdown"}
            if int(holdout["trades"]) < 3:
                return {"accepted": False, "reason": "Insufficient active trades"}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"accepted": False, "reason": "Malformed or incomplete validation evidence"}
    return {"accepted": True, "reason": "Disjoint historical gate passed; forward paper test still required",
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
