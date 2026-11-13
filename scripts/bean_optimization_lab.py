"""Deterministic, credential-free BEAN strategy laboratory for AIscend.

Research only. Does not place orders or modify a live decision policy.
Uses diverse simulated market regimes, held-out seeds, and trading friction.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import math
import random
from pathlib import Path

SCHEMA = "aiscend.bean.optimizer.v1"
SEED = 26010
REGIMES = ("bull", "bear", "sideways", "whipsaw")
BASELINE = {"entry": 0.002, "exit": -0.012, "allocation": 0.55}
FEE = 0.004  # combined fee/slippage per trade, deliberately conservative


def market(seed: int, regime: str, periods: int = 300) -> list[float]:
    rng = random.Random(seed)
    price = 1.0
    path = [price]
    for index in range(periods):
        drift = {"bull": 0.0018, "bear": -0.0018,
                 "sideways": 0.0, "whipsaw": 0.0}[regime]
        cycle = 0.012 * math.sin(index / 7.0) if regime == "whipsaw" else 0.0
        shock = rng.gauss(0, 0.019)
        price = max(0.01, price * (1.0 + drift + cycle + shock))
        path.append(price)
    return path


def simulate(prices: list[float], policy: dict) -> dict:
    cash, qty, peak, trough, trades = 1.0, 0.0, 1.0, 0.0, 0
    for index in range(2, len(prices)):
        price = prices[index]
        signal = prices[index - 1] / prices[index - 2] - 1
        if qty > 0 and signal < policy["exit"]:
            gross = qty * price
            cash += gross * (1.0 - FEE)
            qty = 0.0
            trades += 1
        elif cash > 0 and signal > policy["entry"]:
            amount = cash * policy["allocation"]
            qty += amount * (1.0 - FEE) / price
            cash -= amount
            trades += 1
        nav = cash + qty * price
        peak = max(peak, nav)
        trough = max(trough, 1.0 - nav / peak)
    equity = cash + qty * prices[-1] * (1.0 - FEE if qty else 1.0)
    return {"net": equity, "drawdown": trough, "trades": trades,
            "return": equity - 1.0}


def evaluate(policy: dict, seeds: range) -> dict:
    runs = [simulate(market(seed, regime), policy)
            for seed in seeds for regime in REGIMES]
    nets = sorted(item["net"] for item in runs)
    n = len(nets)
    mean = sum(nets) / n
    worst_quartile = sum(nets[:max(1, n // 4)]) / max(1, n // 4)
    drawdown = sum(item["drawdown"] for item in runs) / n
    trade_count = sum(item["trades"] for item in runs) / n
    # Reliability first: low-tail liquidation value and drawdown penalize fragility.
    score = 0.55 * worst_quartile + 0.45 * mean - 0.5 * drawdown
    return {"score": round(score, 8), "mean_net": round(mean, 8),
            "worst_quartile_net": round(worst_quartile, 8),
            "mean_drawdown": round(drawdown, 8),
            "mean_trades": round(trade_count, 3),
            "loss_frequency": round(sum(x < 1.0 for x in nets) / n, 6),
            "runs": n}


def candidates():
    yield BASELINE
    for entry, exit_signal, allocation in itertools.product(
            (0.002, 0.006, 0.012, 0.02),
            (-0.006, -0.012, -0.025, -0.045),
            (0.25, 0.5, 0.75)):
        yield {"entry": entry, "exit": exit_signal, "allocation": allocation}


def experiment() -> dict:
    train_seeds = range(SEED, SEED + 8)
    holdout_seeds = range(SEED + 500, SEED + 512)
    ranked = sorted(((evaluate(p, train_seeds)["score"], p) for p in candidates()),
                    key=lambda pair: pair[0], reverse=True)
    incumbent_train = evaluate(BASELINE, train_seeds)
    baseline_holdout = evaluate(BASELINE, holdout_seeds)
    winner_policy = ranked[0][1]
    challenger_holdout = evaluate(winner_policy, holdout_seeds)
    # Pre-committed, strict holdout gate. This is NOT evidence of real profits.
    accepted = (
        winner_policy != BASELINE
        and challenger_holdout["score"] > baseline_holdout["score"] + 0.01
        and challenger_holdout["worst_quartile_net"] >=
            baseline_holdout["worst_quartile_net"]
        and challenger_holdout["mean_drawdown"] <=
            baseline_holdout["mean_drawdown"] + 0.01
    )
    return {"schema": SCHEMA, "research_only": True,
            "market": "synthetic_only", "friction_per_trade": FEE,
            "train_seed_start": SEED, "holdout_seed_start": SEED + 500,
            "regimes": REGIMES, "candidate_count": len(list(candidates())),
            "baseline_policy": BASELINE, "baseline_train": incumbent_train,
            "baseline_holdout": baseline_holdout,
            "challenger_policy": winner_policy,
            "challenger_holdout": challenger_holdout,
            "accept_for_review": accepted,
            "limitation": "Synthetic holdout may not predict live profitability. "
                          "No runtime strategy or trading permission is changed."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/bean-optimization.json")
    args = parser.parse_args()
    report = experiment()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"accepted_for_review": report["accept_for_review"],
                      "baseline_holdout": report["baseline_holdout"],
                      "challenger_holdout": report["challenger_holdout"]},
                     indent=2))


if __name__ == "__main__":
    main()
