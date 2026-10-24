"""Freeze the entire Coinbase public spot catalog and merge every five-year shard.

Read-only research, no authenticated endpoints or order permissions.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

from src.universe_research import discover_public_spot, _catalog_hash, shard_for
from scripts.bean_five_year_ledger import store_report, audit_db, canonical


def capture_catalog(output, *, discover=discover_public_spot):
    listing = discover()
    products = listing["eligible_quote_markets"]
    if not products or products != sorted(set(products)):
        raise ValueError("INVALID_PUBLIC_CATALOG")
    report = {
        "schema": "aiscend.bean.five_year.catalog.v1",
        "eligible_quote_markets": products,
        "catalog_hash": _catalog_hash(products),
        "count": len(products),
        "source": "Coinbase public /products; USD and USDC, online spot",
        "account_executable_verified": False,
        "promotion_authorized": False,
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(report) + "\n", encoding="utf-8")
    return report


def aggregate(catalog_file, shard_files, db_path, summary_path):
    catalog = json.loads(Path(catalog_file).read_text(encoding="utf-8"))
    products = catalog["eligible_quote_markets"]
    catalog_hash = _catalog_hash(products)
    if not products or catalog_hash != catalog["catalog_hash"]:
        raise ValueError("CATALOG_HASH_MISMATCH")
    parts = [json.loads(Path(p).read_text(encoding="utf-8"))
             for p in shard_files]
    if not parts:
        raise ValueError("NO_SHARD_RESULTS")
    count = parts[0]["shards"]
    indices = [p["shard_index"] for p in parts]
    if len(parts) != count or sorted(indices) != list(range(count)):
        raise ValueError("MISSING_OR_DUPLICATED_SHARDS")
    results = {}
    for part in parts:
        index = part["shard_index"]
        if (part.get("schema") != "aiscend.bean.five_year.walkforward.v2"
                or part["catalog_hash"] != catalog_hash
                or part["eligible_quote_markets"] != products
                or part["catalog_size"] != len(products)
                or part["shards"] != count or part["promotion_authorized"] is not False):
            raise ValueError("SHARD_CATALOG_MISMATCH")
        expected = {p for p in products if shard_for(p, count) == index}
        if set(part["markets"]) != expected or part["attempted_count"] != len(expected):
            raise ValueError("SHARD_MISSING_OR_EXTRA_MARKETS")
        if set(results).intersection(part["markets"]):
            raise ValueError("DUPLICATED_MARKET_IN_MULTIPLE_SHARDS")
        results.update(part["markets"])
    if set(results) != set(products):
        raise ValueError("INCOMPLETE_PUBLIC_MARKET_CATALOG")
    # Replay ALL outcomes, not just winning or fully covered markets.
    stored = store_report(db_path, {"markets": results})
    audit = audit_db(db_path)
    if audit["outcome_count"] != len(products):
        raise ValueError("LEDGER_DID_NOT_RETAIN_EVERY_MARKET")
    counts = dict(sorted(Counter(r["status"] for r in results.values()).items()))
    leaders = sorted(
        ({"product":p,"net_return":r["net_return"],
          "benchmark":r["continuous_buy_hold_return"],
          "folds":r["fold_count"],"won_vs_benchmark":
              r["net_return"] > r["continuous_buy_hold_return"]}
         for p,r in results.items()
         if r["status"] == "HISTORICAL_WALKFORWARD_RESEARCH"),
        key=lambda x:(-x["net_return"],x["product"]))
    summary = {
        "schema": "aiscend.bean.five_year.universe.aggregate.v1",
        "catalog_hash": catalog_hash,
        "catalog_size": len(products), "attempted_count": len(results),
        "shards": count, "status_counts": counts,
        "research_rankings_only": leaders,
        "outcomes_saved": audit["outcome_count"],
        "full_experiments_saved": len(audit["experiments"]),
        "all_outcomes_in_sqlite": True,
        "ledger_file": Path(db_path).name,
        "promotion_authorized": False,
        "limitations": "Historical strategy research, subject to selection bias. "
                       "Partial histories normal. No forward or funded profitability.",
    }
    dest = Path(summary_path)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(canonical(summary)+"\n",encoding="utf-8")
    print(json.dumps({"catalog_size":len(products),
                      "status_counts":counts,"stored":audit["outcome_count"],
                      "research_winners":sum(x["won_vs_benchmark"] for x in leaders),
                      "promotion_authorized":False},sort_keys=True))
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot-catalog",type=Path)
    g.add_argument("--aggregate",nargs="+",type=Path)
    g.add_argument("--bridge-input",type=Path)
    p.add_argument("--catalog",type=Path)
    p.add_argument("--db",type=Path)
    p.add_argument("--summary",type=Path)
    p.add_argument("--bridge-output",type=Path)
    args=p.parse_args(argv)
    if args.snapshot_catalog:
        report=capture_catalog(args.snapshot_catalog)
        print(json.dumps({"catalog_size":report["count"],
                          "catalog_hash":report["catalog_hash"]},
                         sort_keys=True))
    elif args.bridge_input:
        if not args.bridge_output:
            p.error("--bridge-input requires --bridge-output")
        summary=json.loads(args.bridge_input.read_text(encoding="utf-8"))
        if (summary.get("schema")!="aiscend.bean.five_year.universe.aggregate.v1"
                or summary.get("promotion_authorized") is not False):
            raise ValueError("INVALID_BRIDGE_RESEARCH_SNAPSHOT")
        observation={"positions":{}, "rotation_reviews":[],
                     "net_liquidation":None,
                     "market_radar":{
                         "prediction":"HISTORICAL_ONLY_NOT_LIVE",
                         "catalog_hash":summary["catalog_hash"],
                         "all_market_attempts":summary["attempted_count"],
                         "status_counts":summary["status_counts"],
                         "history_research_leaders":
                             summary["research_rankings_only"][:10],
                         "research_ledger":summary["ledger_file"],
                         "thesis":"Read-only BEAN audit of five-year full-universe "
                                  "hypothetical backtests; no funded order authority"}}
        args.bridge_output.parent.mkdir(parents=True,exist_ok=True)
        args.bridge_output.write_text(canonical(observation)+"\n",encoding="utf-8")
        print("Prepared BEAN read-only complete-catalog advisory input")
    else:
        if not args.catalog or not args.db or not args.summary:
            p.error("Aggregation requires --catalog, --db, and --summary")
        aggregate(args.catalog,args.aggregate,args.db,args.summary)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
