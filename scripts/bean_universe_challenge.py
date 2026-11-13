"""Breadth-first Coinbase research across a rotating catalog cohort.

The eligible Coinbase catalog is not an account-specific tradeability report.
The 60-day historical cohort excludes assets with missing or incomplete bars
rather than inventing price observations. No wallet orders are made.
"""
from __future__ import annotations
import argparse
import json
import math
import time
from pathlib import Path

from scripts.bean_historical_replay import (
    PAIRS, discover_products, fetch_candles,
)
from scripts.bean_evolution_lab import evolve

HOUR = 3600


def select_cohort(products, *, baseline=PAIRS, per_run=10, shard=0):
    catalog = sorted(set(products))
    stable = [p for p in baseline if p in catalog]
    others = [p for p in catalog if p not in stable]
    shard_count = max(1, math.ceil(len(others) / per_run))
    shard = int(shard) % shard_count
    chosen = others[shard * per_run:(shard + 1) * per_run]
    return stable + chosen, shard_count


def collect_cohort(start, end, products, *, shard=0, per_run=10,
                   candle_fn=fetch_candles):
    chosen, shard_count = select_cohort(products, shard=shard, per_run=per_run)
    hours = list(range(start, end, HOUR))
    good = {}
    skipped = {}
    for product in chosen:
        try:
            observations = candle_fn(product, start, end)
            missing = [t for t in hours if t not in observations]
            if missing:
                skipped[product] = f"Missing {len(missing)} of {len(hours)} hours"
                continue
            good[product] = observations
        except Exception as exc:
            skipped[product] = str(exc)[:180]
    result = {
        "schema": "bean.coinbase.research.cohort.v1",
        "catalog_size": len(products), "shard": shard % shard_count,
        "shards_total": shard_count, "attempted": chosen,
        "included": list(good), "skipped": skipped,
        "coverage": "Current publicly online Coinbase USD/USDC products. NOT historical-survivorship-neutral.",
    }
    if len(good) < 2 or "BTC-USD" not in good:
        return None, result
    dataset = {"source": "Coinbase public candles", "schema": "bean.coinbase.hourly.v1",
               "pairs": list(good), "start": start, "end": end,
               "bars": [{"ts": ts, "prices": {p: good[p][ts] for p in good}}
                        for ts in hours]}
    return dataset, result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", type=Path, default=Path("artifacts"))
    p.add_argument("--per-run", type=int, default=10)
    p.add_argument("--shard", type=int)
    p.add_argument("--generations", type=int, default=6)
    p.add_argument("--population", type=int, default=36)
    a = p.parse_args()
    if not 1 <= a.per_run <= 30:
        raise ValueError("per-run must be between 1 and 30 to respect Coinbase rate limits")
    end = int(time.time() // HOUR * HOUR)
    start = end - 60 * 86400
    products = discover_products()
    shard = a.shard if a.shard is not None else (int(time.time() // 86400))
    dataset, manifest = collect_cohort(start, end, products, shard=shard, per_run=a.per_run)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "bean-cohort-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if dataset is None:
        print(json.dumps({"status": "INSUFFICIENT_HISTORY", **manifest}, indent=2))
        return
    report = evolve(dataset, generations=a.generations,
                    population=a.population, seed=6410)
    (a.out_dir / "bean-cohort-evolution.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (a.out_dir / "bean-cohort-dataset.json").write_text(
        json.dumps(dataset, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "EVALUATED", "attempted": manifest["attempted"],
        "included": manifest["included"], "skipped": manifest["skipped"],
        "candidates": report["candidates_tested"],
        "holdout_net": report["final_holdout"]["net_return"],
        "historically_profitable": report["historically_profitable"],
        "beats_benchmarks": report["beats_both_benchmarks"],
        "not_release_ready": not report["release_ready"],
    }, indent=2))


if __name__ == "__main__":
    main()
