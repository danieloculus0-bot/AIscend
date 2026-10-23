"""Full public Coinbase USD/USDC spot-market BEAN Q-ledger research.

No authenticated account, execution API, private key, transfers or real orders.
Native six-hour candles use ~2 API reads per 90-day market rather than
hundreds of unnecessary five-minute reads. Missing candles are not invented.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlencode, quote

from .bean_strategy_lab import _get_json
from .universe_research import discover_public_spot, shard_for, _catalog_hash
from .bean_profit_lab import experiment, Ledger, chain

BAR = 21600
MAX_CANDLES = 280


def fetch_history(product, *, days=90, fetcher=_get_json, clock=None):
    """Read complete Coinbase six-hour OHCV without crossing partial candles."""
    if not 45 <= days <= 120:
        raise ValueError("Requires 45-120 calendar days")
    end = int((clock or time.time)() // BAR) * BAR
    start = end - days * 86400
    out = {}
    for chunk in range(start, end, MAX_CANDLES * BAR):
        stop = min(chunk + MAX_CANDLES * BAR, end)
        query = urlencode({
            "start": datetime.fromtimestamp(chunk, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(stop, timezone.utc).isoformat(),
            "granularity": BAR,
        })
        url = ("https://api.exchange.coinbase.com/products/" +
               quote(product, safe="-") + "/candles?" + query)
        response = fetcher(url)
        if not isinstance(response, list):
            raise ValueError("Coinbase returned no six-hour candle list")
        for row in response:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            ts = int(row[0])
            if chunk <= ts < stop and ts % BAR == 0:
                item = {"ts": ts, "open": float(row[3]),
                        "close": float(row[4]), "volume": float(row[5])}
                if ts in out and out[ts] != item:
                    raise ValueError("Conflicting Coinbase duplicate timestamp")
                out[ts] = item
    return out


def run_shard(*, shards=8, shard_index=0, days=90, fee_bps=60,
              fetcher=_get_json, history_fetcher=None, root=None, workers=3):
    if not 1 <= shards <= 64 or not 0 <= shard_index < shards:
        raise ValueError("Invalid full-universe shard")
    if not 1 <= workers <= 4:
        raise ValueError("Research worker count must be 1-4")
    catalog = discover_public_spot(fetcher=fetcher)
    pairs = catalog["eligible_quote_markets"]
    selected = [p for p in pairs if shard_for(p, shards) == shard_index]
    root = Path(root or "artifacts/bean-sixhour")
    root.mkdir(parents=True, exist_ok=True)
    reports = {}

    def analyze(product):
        try:
            data = (history_fetcher(product, days=days)
                    if history_fetcher else
                    fetch_history(product, days=days, fetcher=fetcher))
            result = experiment(product, data, fee_bps=fee_bps,
                                already_aggregated=True)
            tip = result.get("ledger_tip")
            if tip:
                ledger = Ledger()
                ledger.entries = result.pop("ledger_events")
                ledger.tip = tip
                if chain(ledger.entries) != tip:
                    raise ValueError("BEAN ledger replay changed a hash")
                ledger.save(root / (product.replace("-", "_") + ".jsonl"))
            return product, result
        except Exception as exc:
            return product, {"status": "DATA_UNAVAILABLE",
                             "reason_type": type(exc).__name__,
                             "promotion_authorized": False}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        tasks = [executor.submit(analyze, product) for product in selected]
        for task in as_completed(tasks):
            product, report = task.result()
            reports[product] = report

    return {"schema": "aiscend.bean.sixhour.universe.v1",
            "shards": shards, "shard_index": shard_index,
            "days": days, "fee_bps": fee_bps, "spread_bps": 10,
            "source": "Coinbase Exchange native six-hour OHCV, hypothetical orders",
            "catalog_hash": _catalog_hash(pairs), "catalog": catalog,
            "attempted": len(selected),
            "status_counts": dict(Counter(r["status"] for r in reports.values())),
            "products": dict(sorted(reports.items())),
            "promotion_authorized": False}


def aggregate(reports):
    if not reports:
        raise ValueError("No BEAN reports")
    first = reports[0]
    expected_shards = first["shards"]
    indices = [r["shard_index"] for r in reports]
    if len(reports) != expected_shards or sorted(indices) != list(range(expected_shards)):
        raise ValueError("Missing, duplicate or inconsistent BEAN shards")
    if any((r["shards"] != expected_shards or
            r["catalog_hash"] != first["catalog_hash"] or
            r["catalog"]["eligible_quote_markets"] !=
                first["catalog"]["eligible_quote_markets"] or
            r["fee_bps"] != first["fee_bps"] or r["days"] != first["days"] or
            r["spread_bps"] != first["spread_bps"]) for r in reports):
        raise ValueError("Incompatible Coinbase catalog or experiment assumptions")

    all_results = {}
    for report in reports:
        for product, row in report["products"].items():
            if product in all_results or shard_for(product, expected_shards) != report["shard_index"]:
                raise ValueError("Duplicate or wrongly sharded market")
            all_results[product] = row
    if set(all_results) != set(first["catalog"]["eligible_quote_markets"]):
        raise ValueError("Missing market results in full BEAN universe")

    # All opened holdouts are reported. NEVER retrospectively promote the
    # best one from a hundreds-market search as a validated live strategy.
    summaries = {
        product: {
            "status": row["status"], "champion": row.get("champion"),
            "eligible_before_holdout": row.get("eligible_before_holdout"),
            "holdout_net": (row.get("holdout") or {}).get("net_return"),
            "holdout_buy_hold": (row.get("holdout_buy_hold") or {}).get("net_return"),
            "ledger_tip": row.get("ledger_tip")}
        for product, row in sorted(all_results.items())
    }
    return {"schema": "aiscend.bean.sixhour.aggregate.v1",
            "catalog_hash": first["catalog_hash"], "market_count": len(all_results),
            "shards": expected_shards,
            "status_counts": dict(Counter(v["status"] for v in all_results.values())),
            "markets": summaries, "promotion_authorized": False,
            "note": "Opened historical holdouts and market-selection bias: no forward profitability proven"}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--fee-bps", type=float, default=60.)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--root", type=Path, default=Path("artifacts/bean-sixhour"))
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/bean-sixhour-shard.json"))
    parser.add_argument("--aggregate", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.aggregate:
        result = aggregate([json.loads(p.read_text()) for p in args.aggregate])
    else:
        result = run_shard(shards=args.shards, shard_index=args.shard_index,
                           days=args.days, fee_bps=args.fee_bps,
                           workers=args.workers, root=args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(
        result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({k: result.get(k) for k in
                      ("market_count", "attempted", "status_counts",
                       "promotion_authorized")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
