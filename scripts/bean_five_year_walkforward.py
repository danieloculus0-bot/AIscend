"""Five-year public Coinbase 6h walk-forward strategy and BEAN learning experiment.

No orders, account secrets, or live strategy promotion. BEAN adjusts rule
preferences only AFTER each historical test window has closed. All market
observations, selected rules and hypothetical fills are strictly causal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlencode, quote

from src.bean_profit_lab import Ledger, chain
from src.bean_strategy_lab import _get_json
from src.published_strategy_research import (
    BAR, RULES, WARMUP, _signal, simulate, normalize,
    DEFAULT_FEE_BPS, DEFAULT_SPREAD_BPS,
)

TRAIN_BARS = 1460                  # 365 days on 6h bars
TEST_BARS = 360                    # 90-day sealed test windows
FIRST_TEST = WARMUP + TRAIN_BARS   # warm-up cannot be training performance
MAX_CANDLES = 280
MODEL = "five_year_six_hour_walkforward_bean_q_v1"


def fetch_five_year(product, *, years=5, fetcher=_get_json, clock=None):
    """Public-only 6h OHLCV, 280 bars per request to honor Exchange 300 cap."""
    now = datetime.fromtimestamp(float((clock or time.time)()), timezone.utc)
    try:
        start_date = now.replace(year=now.year-years)
    except ValueError:
        start_date = now.replace(year=now.year-years, day=28)
    end = int(now.timestamp()//BAR)*BAR
    start = int(start_date.timestamp()//BAR)*BAR
    data = {}
    requests = 0
    for a in range(start, end, MAX_CANDLES*BAR):
        b = min(a+MAX_CANDLES*BAR, end)
        query = urlencode({
            "start": datetime.fromtimestamp(a, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(b, timezone.utc).isoformat(),
            "granularity": BAR})
        url = ("https://api.exchange.coinbase.com/products/"
               +quote(product, safe="-")+"/candles?"+query)
        payload = fetcher(url)
        requests += 1
        if not isinstance(payload, list):
            raise ValueError("DATA_UNAVAILABLE")
        for raw in payload:
            if not isinstance(raw, (list, tuple)) or len(raw) < 6:
                continue
            try:
                ts = int(raw[0])
                row = {"ts": ts, "low": float(raw[1]), "high": float(raw[2]),
                       "open": float(raw[3]), "close": float(raw[4]),
                       "volume": float(raw[5])}
            except (TypeError, ValueError, OverflowError):
                continue
            if a <= ts < b and ts % BAR == 0:
                if ts in data and data[ts] != row:
                    raise ValueError("CONFLICTING_CANDLES")
                data[ts] = row
    return {"product": product, "start": start, "end": end,
            "expected_bars": (end-start)//BAR,
            "received_bars": len(data), "requests": requests, "data": data}


def contiguous_runs(raw):
    """Separate missing historical intervals; NEVER replace missing candles."""
    runs, current, last = [], {}, None
    for ts, row in sorted(raw.items()):
        if last is not None and ts != last+BAR:
            if current:
                runs.append(current)
            current = {}
        current[ts] = row
        last = ts
    if current:
        runs.append(current)
    return runs


def _score(result):
    if result["roundtrips"] < 1:
        return -math.inf
    return result["net_return"] - .4*result["max_drawdown"]


def _journal(rows, rule, first, end, *, fee_bps, spread_bps):
    """Exact next-open hypothetical BUY/SELL ledger; open lot remains a mark."""
    cost = (fee_bps+spread_bps/2)/10000.
    capital, held, entry, entries = 1., 0., None, []
    for i in range(first,end):
        enter, exit_now = _signal(rows, i-1, rule)
        open_price = rows[i]["open"]
        if held and exit_now:
            realized = held*open_price*(1-cost)
            entries.append({"entry_ts": entry["ts"], "exit_ts": rows[i]["ts"],
                            "entry_open": entry["price"], "exit_open": open_price,
                            "fee_net_trade_return": round(realized/entry["capital"]-1,8),
                            "winning_trade": realized > entry["capital"],
                            "end_type": "next_open_signal_exit"})
            capital, held, entry = realized, 0., None
        elif not held and enter:
            entry = {"ts": rows[i]["ts"], "price": open_price, "capital": capital}
            held = capital*(1-cost)/open_price
            capital = 0.
    nav = capital if not held else held*rows[end-1]["close"]*(1-cost)
    return entries, nav, bool(held)


def walkforward(raw, *, product="UNKNOWN-USD", fee_bps=DEFAULT_FEE_BPS,
                spread_bps=DEFAULT_SPREAD_BPS, start_capital=1000.,
                train_bars=TRAIN_BARS, test_bars=TEST_BARS, recall_db=None):
    """Choose rules on trailing history; learn only from already-ended folds."""
    if train_bars < 360 or test_bars < 16 or not start_capital > 0:
        raise ValueError("INVALID_CONFIGURATION")
    try:
        rows = normalize(raw)
    except ValueError as exc:
        if str(exc) == "PARTIAL":
            return {"status": "PARTIAL", "promotion_authorized": False}
        raise
    first = WARMUP+train_bars
    if len(rows)-first < test_bars:
        return {"status": "INSUFFICIENT_HISTORY", "bars": len(rows),
                "promotion_authorized": False}
    fee = (fee_bps+spread_bps/2)/10000.
    if not math.isfinite(fee) or not 0 <= fee < 1:
        raise ValueError("INVALID_COST")
    ledger = Ledger()
    ledger.append("pre_register", model=MODEL, product=product,
                  start=rows[0]["ts"], end=rows[-1]["ts"],
                  train_bars=train_bars, test_bars=test_bars,
                  fee_bps=fee_bps, spread_bps=spread_bps,
                  strategies=[list(rule) for rule in RULES])
    # Recall is allowed only when an entire previous experiment finished
    # before the very FIRST held-out period. Overlapping research is not memory.
    if recall_db is not None:
        from scripts.bean_five_year_recall import recall_prior
        recalled = recall_prior(recall_db, product=product,
                                before_ts=rows[first]["ts"])
    else:
        recalled = {"status":"NO_ELIGIBLE_PRIOR", "prior_scores":{},
                    "source_experiments":[], "promotion_authorized":False}
    q = {rule: float(recalled["prior_scores"].get(repr(rule), 0.))
         for rule in RULES}
    ledger.append("research_memory_recall", status=recalled["status"],
                  cutoff_ts=rows[first]["ts"],
                  source_experiments=recalled["source_experiments"],
                  recalled_scores=recalled["prior_scores"])
    folds, trades = [], []
    equity, buy_hold = 1., 1.
    chosen_active, wins = 0, 0
    for i in range(first, len(rows)-test_bars+1, test_bars):
        cut = i+test_bars
        train_from = i-train_bars
        train = {r: simulate(rows,r,start=train_from,end=i,
                             fee_bps=fee_bps,spread_bps=spread_bps)
                 for r in RULES}
        # Q contains ONLY outcomes from past completed test windows.
        scores = {r: _score(train[r]) + .25*q[r]
                  if math.isfinite(_score(train[r])) else -math.inf
                  for r in RULES}
        qualified = [r for r in RULES if scores[r] > 0]
        chosen = (max(qualified, key=lambda r:(scores[r],-RULES.index(r)))
                  if qualified else None)
        evidence = {"trained_through":rows[i-1]["ts"], "test_start":rows[i]["ts"],
                    "test_end":rows[cut-1]["ts"],
                    "winner":list(chosen) if chosen else "cash",
                    "training_scores":{str(k):round(v,8) if math.isfinite(v)
                                       else None for k,v in scores.items()},
                    "bean_q_before":{str(k):round(v,8) for k,v in q.items()}}
        ledger.append("locked_before_test_window",**evidence)
        # Future held-out results only become learner evidence AFTER lock.
        realized = {r:simulate(rows,r,start=i,end=cut,fee_bps=fee_bps,
                              spread_bps=spread_bps) for r in RULES}
        net = realized[chosen]["net_return"] if chosen else 0.
        if chosen:
            entries, journal_nav, still_held = _journal(
                rows,chosen,i,cut,fee_bps=fee_bps,spread_bps=spread_bps)
            if abs((journal_nav-1)-net)>2e-7:
                raise ValueError("JOURNAL_SIMULATOR_DISAGREEMENT")
            for entry in entries:
                trades.append({"window":len(folds),"rule":list(chosen),**entry})
            chosen_active += 1
            wins += int(net>0)
        hold_gain = (rows[cut-1]["close"]/rows[i]["open"]*(1-fee)**2-1)
        equity *= 1+net
        buy_hold *= 1+hold_gain
        ledger.append("after_test_window_closed",window=len(folds),
                      chosen=list(chosen) if chosen else "cash",
                      fee_net_return=net, buy_hold_net_return=round(hold_gain,8),
                      trained_through=rows[i-1]["ts"],
                      observed_through=rows[cut-1]["ts"])
        # Train on counterfactual paper choices only AFTER their bars close.
        for r in RULES:
            contribution = (realized[r]["net_return"]
                            -.4*realized[r]["max_drawdown"])
            q[r] = .70*q[r]+.30*contribution
        ledger.append("bean_feedback_postclose",window=len(folds),
                      observed_through=rows[cut-1]["ts"],
                      q_after={str(k):round(v,8) for k,v in q.items()})
        folds.append({**evidence,"window":len(folds),
                      "fee_net_return":net,
                      "buy_hold_window_net_return":round(hold_gain,8),
                      "closed_trades":(realized[chosen]["roundtrips"]
                                       if chosen else 0),
                      "winning_trades":realized[chosen]["wins"] if chosen else 0,
                      "open_position_marked":realized[chosen]["open_position"]
                                            if chosen else False,
                      "simulated_equity":round(start_capital*equity,4)})
    if chain(ledger.entries)!=ledger.tip:
        raise ValueError("LEDGER_NOT_REPRODUCIBLE")
    total_trades = sum(f["closed_trades"] for f in folds)
    total_wins = sum(f["winning_trades"] for f in folds)
    # A true passive benchmark buys ONCE and liquidates ONCE. Paying fresh
    # fees at every 90-day fold makes buy-and-hold falsely look weaker.
    passive_exit_index = first + len(folds)*test_bars - 1
    continuous_buy_hold = (
        rows[passive_exit_index]["close"]/rows[first]["open"]*(1-fee)**2-1)
    return {"status":"HISTORICAL_WALKFORWARD_RESEARCH",
            "product":product,"bars":len(rows),"folds":folds,
            "fold_count":len(folds),"simulated_start":start_capital,
            "simulated_final":round(start_capital*equity,4),
            "net_return":round(equity-1,8),
            "fold_rebalanced_buy_hold_return":round(buy_hold-1,8),
            "continuous_buy_hold_return":round(continuous_buy_hold,8),
            "active_folds":chosen_active,"positive_folds":wins,
            "completed_roundtrips":total_trades,
            "winning_roundtrips":total_wins,"trade_journal":trades,
            "bean_q_final":{str(k):round(v,8) for k,v in q.items()},
            "recalled_prior_sources":recalled["source_experiments"],
            "recalled_prior_status":recalled["status"],
            "ledger_tip":ledger.tip,"ledger":ledger.entries,
            "promotion_authorized":False,
            "limitations":("Historical rolling adaptation and hypothetical fills only; "
                           "fold-reset cash and buy-hold benchmark; open positions "
                           "marked with liquidation fees. No authenticated fills "
                           "or verified future profitability.")}


def evaluate_product_history(fetched, *, recall_db=None):
    runs=contiguous_runs(fetched["data"])
    # Keep all gaps visible, never fabricate missing six-hour bars.
    longest=max(runs,key=len) if runs else {}
    expected=fetched["expected_bars"]
    coverage=fetched["received_bars"]/expected if expected else 0.
    common={"product":fetched["product"],"expected_bars":expected,
            "received_bars":fetched["received_bars"],
            "history_coverage":round(coverage,6),
            "contiguous_runs":len(runs),"longest_run_bars":len(longest),
            "history_status":"COMPLETE" if fetched["received_bars"]==expected
                else "PARTIAL", "promotion_authorized":False}
    if not longest or len(longest)<FIRST_TEST+TEST_BARS:
        return {**common,"status":"INSUFFICIENT_HISTORY"}
    result=walkforward(longest,product=fetched["product"],
                       recall_db=recall_db)
    return {**result,**common}


def main(argv=None):
    from collections import Counter
    from src.universe_research import _catalog_hash, shard_for

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True,
                        help="One immutable full Coinbase public spot catalog shared by EVERY shard")
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--recall-db", type=Path,
                        help="Optional audited prior BEAN research; strict no-overlap cutoff")
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/bean-5y-walkforward.json"))
    parser.add_argument("--bridge-snapshot", type=Path,
                        help="Read-only observation for actual BEAN Core")
    args = parser.parse_args(argv)
    if args.years != 5:
        parser.error("Experiment must cover exactly five calendar years")
    if not 1 <= args.shards <= 64 or not 0 <= args.shard_index < args.shards:
        parser.error("Invalid complete-universe shard")
    snapshot = json.loads(args.catalog.read_text(encoding="utf-8"))
    if snapshot.get("schema") != "aiscend.bean.five_year.catalog.v1":
        raise ValueError("INVALID_CATALOG_SCHEMA")
    all_products = snapshot.get("eligible_quote_markets")
    if (not isinstance(all_products, list) or not all_products or
            all_products != sorted(set(all_products)) or
            any(not isinstance(p, str) or not p.endswith(("-USD", "-USDC"))
                for p in all_products)):
        raise ValueError("INVALID_COMPLETE_CATALOG")
    catalog_hash = _catalog_hash(all_products)
    if catalog_hash != snapshot.get("catalog_hash"):
        raise ValueError("CATALOG_HASH_MISMATCH")
    selected = [p for p in all_products
                if shard_for(p, args.shards) == args.shard_index]
    results = {}
    for product in selected:
        try:
            fetched = fetch_five_year(product, years=args.years)
            results[product] = evaluate_product_history(
                fetched, recall_db=args.recall_db)
        except Exception as exc:
            results[product] = {"product": product, "status": "DATA_UNAVAILABLE",
                                "error_type": type(exc).__name__,
                                "promotion_authorized": False}
    counts = dict(sorted(Counter(r["status"] for r in results.values()).items()))
    report = {"schema": "aiscend.bean.five_year.walkforward.v2",
              "catalog_hash": catalog_hash, "catalog_size": len(all_products),
              "eligible_quote_markets": all_products,
              "shards": args.shards, "shard_index": args.shard_index,
              "attempted_count": len(selected), "status_counts": counts,
              "simulated_start_capital": 1000.0,
              "research_only": True, "promotion_authorized": False,
              "markets": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True,
                                      allow_nan=False) + "\n", encoding="utf-8")
    if args.bridge_snapshot:
        observation = {
            "positions": {}, "rotation_reviews": [], "net_liquidation": None,
            "market_radar": {
                "prediction": "FIVE_YEAR_FULL_UNIVERSE_RESEARCH_ONLY",
                "catalog_hash": catalog_hash, "shard_index": args.shard_index,
                "attempted": len(selected), "status_counts": counts,
                "thesis": "BEAN advisory only; no funded orders or future profit claims",
                "markets": {p: {"status": r["status"],
                                "net_return": r.get("net_return")}
                            for p, r in results.items()}}}
        args.bridge_snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.bridge_snapshot.write_text(json.dumps(
            observation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"catalog_size": len(all_products),
                      "catalog_hash": catalog_hash, "attempted": len(selected),
                      "shard_index": args.shard_index, "status_counts": counts,
                      "promotion_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
