"""Research every publicly listed, online Coinbase USD/USDC spot market.

Only public Coinbase Exchange data. This is NOT a list of pairs available to
the owner's authenticated Advanced Trade account, and this module cannot trade.
The catalog is snapshotted on each job. Deterministic shards permit full
universe testing while keeping API request concurrency bounded. All actual
selection and strategy scores are causal and fee-inclusive in strategy_research.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time

from .bean_strategy_lab import _get_json
from .strategy_research import historical_bars, validate

CATALOG_URL = "https://api.exchange.coinbase.com/products"
FUNDABLE_QUOTES = frozenset(("USD", "USDC"))


def discover_public_spot(*, fetcher=_get_json):
    """Enumerate the full public spot listing, without a major-coin whitelist.

    Every online spot pair is enumerated; research can model direct USD/USDC
    purchases only. Other quote currencies need an account-verified conversion
    route before they can be considered executable.
    """
    rows = fetcher(CATALOG_URL)
    if not isinstance(rows, list):
        raise ValueError("Coinbase public products response must be a list")
    all_spot, eligible = set(), set()
    excluded = Counter()
    for row in rows:
        if not isinstance(row, dict):
            excluded["invalid_record"] += 1
            continue
        product = str(row.get("id") or "").upper()
        base = str(row.get("base_currency") or "").upper()
        quote = str(row.get("quote_currency") or "").upper()
        product_type = str(row.get("product_type") or row.get("type") or "spot").lower()
        if not product or not base or not quote or product != base+"-"+quote:
            excluded["invalid_identity"] += 1
            continue
        if product_type not in ("spot", ""):
            excluded["non_spot"] += 1
            continue
        all_spot.add(product)
        if str(row.get("status") or "").lower() != "online":
            excluded["offline"] += 1
        elif any(row.get(flag) is True for flag in
                 ("trading_disabled", "cancel_only", "post_only", "limit_only")):
            excluded["not_market_orderable"] += 1
        elif quote not in FUNDABLE_QUOTES:
            excluded["requires_other_quote_route"] += 1
        elif base in FUNDABLE_QUOTES:
            excluded["cash_or_bridge_pair"] += 1
        else:
            eligible.add(product)
    listing = sorted(eligible)
    return {
        "source": "Coinbase Exchange public /products",
        "public_spot_pairs": sorted(all_spot),
        "public_spot_count": len(all_spot),
        "eligible_quote_markets": listing,
        "eligible_count": len(listing),
        "exclusions": dict(sorted(excluded.items())),
        "account_executable_verified": False,
        "quote_policy": "Online, market-orderable USD/USDC spot pairs; no asset whitelist",
    }


def shard_for(product, shards):
    return int(hashlib.sha256(product.encode("ascii")).hexdigest(), 16) % shards


def _catalog_hash(catalog):
    return hashlib.sha256("\n".join(catalog).encode("ascii")).hexdigest()


def run_shard(*, shards=1, shard_index=0, days=21, fee_bps=60.0,
              workers=3, catalog_fetcher=_get_json,
              history_fetcher=historical_bars, evaluator=validate):
    if not 1 <= shards <= 64 or not 0 <= shard_index < shards:
        raise ValueError("Invalid research shard selection")
    if not 7 <= days <= 120:
        raise ValueError("Research must use 7-120 calendar days")
    if not 0 < fee_bps < 10000:
        raise ValueError("Fee in basis points must be positive")
    if not 1 <= workers <= 4:
        raise ValueError("Use 1-4 workers to respect public API limits")
    catalog = discover_public_spot(fetcher=catalog_fetcher)
    products = catalog["eligible_quote_markets"]
    chosen = [p for p in products if shard_for(p, shards) == shard_index]

    def examine(product):
        try:
            candles = history_fetcher(product, days=days)
            report = evaluator(candles, fee_bps=fee_bps)
            return product, report
        except Exception as error:
            return product, {"status": "DATA_UNAVAILABLE",
                             "reason": f"{type(error).__name__}: {str(error)[:160]}"}

    results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(examine, product) for product in chosen]
        for future in as_completed(futures):
            product, result = future.result()
            results[product] = result

    status_counts = Counter(r["status"] for r in results.values())
    return {
        "schema": "aiscend.universe_research.v1",
        "generated_at": time.time(),
        "market_data_source": "Coinbase Exchange public OHLCV; hypothetical fills",
        "catalog": catalog,
        "catalog_hash": _catalog_hash(products),
        "shards": shards,
        "shard_index": shard_index,
        "days": days,
        "fee_bps": fee_bps,
        "attempted_count": len(chosen),
        "status_counts": dict(status_counts),
        "products": dict(sorted(results.items())),
        "promotion_authorized": False,
        "limitation": ("All pairs are PUBLIC listings, not authenticated account permissions. "
                       "Holdouts are historical, not independent forward fills."),
    }


def aggregate_shards(reports):
    """Coverage is complete only with one report from every consistent shard."""
    if not reports:
        raise ValueError("No shard reports provided")
    count = reports[0]["shards"]
    catalog = reports[0]["catalog"]
    h = reports[0]["catalog_hash"]
    indices = [r["shard_index"] for r in reports]
    if len(reports) != count or sorted(indices) != list(range(count)):
        raise ValueError("Missing or duplicate research shards")
    if any(r["shards"] != count or r["catalog_hash"] != h or
           r["catalog"]["eligible_quote_markets"] != catalog["eligible_quote_markets"]
           or r["days"] != reports[0]["days"] or
           r["fee_bps"] != reports[0]["fee_bps"]
           for r in reports):
        raise ValueError("Research shards did not use the same catalog and assumptions")
    all_results = {}
    for report in reports:
        for product, result in report["products"].items():
            if product in all_results or shard_for(product, count) != report["shard_index"]:
                raise ValueError("Duplicate or incorrectly sharded product")
            all_results[product] = result
    expected = set(catalog["eligible_quote_markets"])
    if set(all_results) != expected:
        raise ValueError("Missing or unexpected market evaluations")
    counts = Counter(r["status"] for r in all_results.values())

    # Choose possible leaders using training ONLY. Do not retrospectively
    # pick the highest holdout across hundreds of trials as a winning strategy.
    training_rank = []
    for product, report in all_results.items():
        champion = report.get("champion")
        training = (report.get("training") or {}).get(champion) or {}
        if (champion and champion != "cash" and training.get("trades", 0) >= 3):
            training_rank.append((training.get("net_return", -1.0),
                                  product, champion))
    training_rank.sort(reverse=True)
    preselected = [
        {"product": product, "policy": policy, "train_return": score,
         "holdout_return": all_results[product]["holdout"][policy]["net_return"],
         "holdout_trades": all_results[product]["holdout"][policy]["trades"],
         "holdout_status": all_results[product]["status"]}
        for score, product, policy in training_rank[:10]
    ]
    coverage = ("COMPLETE" if not
                any(r in counts for r in ("DATA_UNAVAILABLE", "INSUFFICIENT_HISTORY"))
                else "PARTIAL")
    return {
        "schema": "aiscend.universe_research_aggregate.v1",
        "catalog": catalog,
        "catalog_hash": h,
        "shards_verified": count,
        "evaluated_count": len(all_results),
        "data_coverage": coverage,
        "status_counts": dict(counts),
        "training_preselected": preselected,
        "promotion_authorized": False,
        "status": "HISTORICAL_RESEARCH_ONLY",
        "limitation": ("Testing many products creates multiple-comparison risk. "
                       "No candidate is promotable without new unseen windows, "
                       "forward paper results, account permissions and real fills."),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--shards", type=int, default=1)
    p.add_argument("--shard-index", type=int, default=0)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--days", type=int, default=21)
    p.add_argument("--fee-bps", type=float, default=60.0)
    p.add_argument("--aggregate", nargs="+", type=Path)
    p.add_argument("--output", type=Path, default=Path("artifacts/universe-research.json"))
    args = p.parse_args()
    if args.aggregate:
        report = aggregate_shards([
            json.loads(f.read_text(encoding="utf-8")) for f in args.aggregate])
    else:
        report = run_shard(shards=args.shards, shard_index=args.shard_index,
                           days=args.days, fee_bps=args.fee_bps,
                           workers=args.workers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n",
                           encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in
                     ("status", "data_coverage", "evaluated_count", "attempted_count",
                      "status_counts", "promotion_authorized")}, sort_keys=True))


if __name__ == "__main__":
    main()
