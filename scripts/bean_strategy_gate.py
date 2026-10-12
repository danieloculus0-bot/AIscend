"""Fail-closed historical strategy acceptance gate.

Two positive reports are NOT independent just because they have different names.
Reports must identify disjoint holdout AND training windows, fee assumptions,
source fingerprints and numerical cash/BTC benchmarks. This remains a
research-only historical gate, never permission to trade or a profitability proof.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

MIN_STRESS_COST_PER_SIDE = 0.006
FILL_POLICY = "previous_close_signal_next_open_fill"


def _window(report: dict, name: str) -> tuple[int, int]:
    value = report.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"Missing {name} provenance")
    start, end = value.get("start_ts"), value.get("end_ts")
    if (type(start) is not int or type(end) is not int
            or start < 0 or end <= start
            or start % 3600 or end % 3600):
        raise ValueError(f"Invalid hourly {name} bounds")
    return start, end  # Half-open UTC epoch-second interval.


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _finite(value) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a financial measurement")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite financial measurement")
    return result


def _reject(reason: str) -> dict:
    return {"accepted": False, "reason": reason}


def judge(reports: list[dict]) -> dict:
    if len(reports) < 2:
        return _reject("Need at least two independently proven market windows")
    try:
        if any(not isinstance(r, dict) for r in reports):
            return _reject("Malformed report")
        if any(r.get("historical_source") != "Coinbase public candles" for r in reports):
            return _reject("Unverified or synthetic source label")
        strategies = [r.get("strategy") for r in reports]
        if any(not isinstance(s, dict) or not s for s in strategies):
            return _reject("Missing fixed strategy specification")
        if len({json.dumps(s, sort_keys=True) for s in strategies}) != 1:
            return _reject("Different strategies selected: no stable champion")

        training = [_window(r, "training_window") for r in reports]
        holdouts = [_window(r, "holdout_window") for r in reports]
        # No holdout may overlap any training window, even from another report.
        # Otherwise one report's "unseen" bars could have tuned the other.
        for i, holdout in enumerate(holdouts):
            if any(_overlaps(holdout, train) for train in training):
                return _reject("Training and holdout windows overlap across reports")
            if any(_overlaps(holdout, other)
                   for j, other in enumerate(holdouts) if i != j):
                return _reject("Holdout windows overlap; not independent")
        fingerprints = [r.get("dataset_sha256") for r in reports]
        if any(not isinstance(h, str) or len(h) != 64
               or any(c not in "0123456789abcdef" for c in h)
               for h in fingerprints):
            return _reject("Missing canonical dataset SHA-256 provenance")
        if len(set(fingerprints)) != len(fingerprints):
            return _reject("Duplicate dataset fingerprint")

        for r in reports:
            if "BTC-USD" not in r.get("pairs", []):
                return _reject("BTC benchmark market missing")
            if r.get("fill_policy") != FILL_POLICY:
                return _reject("Next-open execution assumption missing")
            if _finite(r.get("cost_per_side")) < MIN_STRESS_COST_PER_SIDE:
                return _reject("Fee and slippage stress is inadequate")
            start = _finite(r.get("starting_bankroll"))
            candidate = r.get("holdout")
            btc = r.get("buy_hold_btc")
            cash = r.get("cash")
            if not all(isinstance(x, dict) for x in (candidate, btc, cash)):
                return _reject("Missing explicit benchmark results")
            final = _finite(candidate.get("final"))
            btc_final = _finite(btc.get("final"))
            cash_final = _finite(cash.get("final"))
            net = _finite(candidate.get("net_return"))
            drawdown = _finite(candidate.get("max_drawdown"))
            trades = candidate.get("trades")
            if start <= 0 or abs(cash_final - start) > 1e-4:
                return _reject("Invalid or inconsistent cash baseline")
            if (final <= max(cash_final, btc_final) or net <= 0
                    or not math.isclose(net, final / start - 1,
                                        rel_tol=1e-4, abs_tol=1e-4)):
                return _reject("Candidate did not beat fee-aware cash and BTC")
            if (r.get("beats_btc_holdout") is not True
                    or r.get("beats_cash_holdout") is not True):
                return _reject("Benchmark verdict contradicts reported values")
            if not 0 <= drawdown <= 0.25:
                return _reject("Excessive or invalid observed drawdown")
            if type(trades) is not int or trades < 1:
                return _reject("No demonstrated active trading edge")
    except (ValueError, TypeError, OverflowError, KeyError):
        return _reject("Malformed or incomplete independent validation evidence")

    return {"accepted": True,
            "reason": "Historical disjoint-window gate passed; untouched future and forward-paper validation still required",
            "candidate": strategies[0],
            "independent_historical_windows": len(reports),
            "release_ready": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--out", type=Path,
                        default=Path("artifacts/bean-promotion-gate.json"))
    args = parser.parse_args()
    verdict = judge([json.loads(p.read_text(encoding="utf-8"))
                     for p in args.reports])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    # Rejection is a research result, not a job failure.


if __name__ == "__main__":
    main()
