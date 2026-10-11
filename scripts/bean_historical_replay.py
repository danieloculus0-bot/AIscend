"""BEAN historical Coinbase replay: offline-safe, walk-forward, fee-aware.

Fetch real hourly Coinbase candles to a JSON evidence file, then compare fixed
policies on unseen chronological bars. No credentials, orders or fabricated data.
Historical candle closes are signals; fills happen at the NEXT hour's open.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PAIRS = ("BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD")  # legacy fixture fallback
QUOTE_ASSETS = {"USD", "USDC"}
COST = 0.006  # fee + adverse fill/slippage each side (stress assumption)
START = 25.0
GOAL = 100000.0


def discover_products(request_fn=urlopen):
    """Discover all online USD/USDC spot markets; do not impose a coin allowlist."""
    req = Request("https://api.exchange.coinbase.com/products",
                  headers={"User-Agent": "BEAN-AIscend-research/1"})
    with request_fn(req, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError("Coinbase product catalog unavailable")
    products = {}
    for row in payload:
        base = str(row.get("base_currency") or "").upper()
        quote = str(row.get("quote_currency") or "").upper()
        product = str(row.get("id") or "").upper()
        if (row.get("status") != "online" or row.get("trading_disabled") is True
                or quote not in QUOTE_ASSETS or not base or not product
                or base in {"USD", "USDC"}):
            continue
        # Keep one quote product per underlying coin to avoid double-counting.
        old = products.get(base)
        if old is None or (quote == "USD" and old.endswith("-USDC")):
            products[base] = product
    return sorted(products.values())


def fetch_candles(product, start, end, request_fn=urlopen):
    out = {}
    cursor = int(start)
    while cursor < end:
        stop = min(end, cursor + 290 * 3600)
        query = urlencode({"start": str(cursor), "end": str(stop),
                           "granularity": "ONE_HOUR", "limit": "300"})
        url = ("https://api.coinbase.com/api/v3/brokerage/market/products/"
               + product + "/candles?" + query)
        req = Request(url, headers={"User-Agent": "BEAN-AIscend-historical-research/1"})
        # Public Coinbase API limits are real: back off on transient 429/5xx
        # responses instead of disguising missing history as an empty market.
        for attempt in range(5):
            try:
                with request_fn(req, timeout=30) as response:
                    body = json.load(response)
                break
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise
                retry_after = getattr(exc, "headers", {}) or {}
                try:
                    retry_seconds = float(retry_after.get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_seconds = 0.0
                time.sleep(min(30.0, max(2.0 * 2 ** attempt, retry_seconds)))
        if request_fn is urlopen:
            time.sleep(0.3)  # avoid bursting through public rate limits
        candles = body.get("candles")
        if not isinstance(candles, list) or not candles:
            raise ValueError("No Coinbase candles for " + product)
        for c in candles:
            ts = int(c["start"])
            op, cl = float(c["open"]), float(c["close"])
            if cursor <= ts < stop and math.isfinite(op) and math.isfinite(cl) and op > 0 and cl > 0:
                out[ts] = {"open": op, "close": cl}
        cursor = stop
    return out


def collect(start, end, pairs=None):
    pairs = tuple(pairs if pairs is not None else PAIRS)
    if not pairs:
        raise ValueError("No valid products to research")
    series = {p: fetch_candles(p, start, end) for p in pairs}
    # Align a real timestamp shared by every product; missing hours are dropped.
    common = sorted(set.intersection(*(set(s) for s in series.values())))
    if len(common) < 120:
        raise ValueError("Insufficient overlapping historical Coinbase observations")
    # Missing hours invalidate a nominally continuous hourly walk-forward.
    if any(b - a != 3600 for a, b in zip(common, common[1:])):
        raise ValueError("Coinbase price history contains missing hourly bars")
    return {"schema": "bean.coinbase.hourly.v1", "source": "Coinbase public candles",
            "pairs": list(pairs), "start": start, "end": end,
            "bars": [{"ts": ts, "prices": {p: series[p][ts] for p in pairs}}
                     for ts in common]}


def run(bars, rule, lookback, threshold, *, starting=START, pairs=None):
    pairs = tuple(pairs if pairs is not None else bars[0]["prices"])
    cash, units, held = float(starting), 0.0, None
    equity, trades, peak, drawdown = [], 0, starting, 0.0
    # Current bar open is executable; signals rely only on closes strictly earlier.
    for i in range(lookback + 1, len(bars)):
        today = bars[i]["prices"]
        previous = bars[i-1]["prices"]
        old = bars[i - lookback - 1]["prices"]
        scores = {p: previous[p]["close"] / old[p]["close"] - 1
                  for p in pairs}
        if rule == "buy_hold":
            chosen = "BTC-USD"
        elif rule == "momentum":
            chosen = max(scores, key=scores.get)
            if scores[chosen] <= threshold:
                chosen = None
        elif rule == "reversal":
            chosen = min(scores, key=scores.get)
            if scores[chosen] >= -threshold:
                chosen = None
        elif rule == "trend":
            # Require both short-term and longer-term trend confirmation.
            long_index = max(0, i - 25)
            long_scores = {
                p: previous[p]["close"] / bars[long_index][
                    "prices"][p]["close"] - 1 for p in pairs
            }
            qualified = [p for p in PAIRS
                         if scores[p] > threshold and long_scores[p] > 0]
            chosen = max(qualified, key=lambda p: scores[p]) if qualified else None
        elif rule == "persistent":
            # Hysteresis avoids liquidating a trend on every small fluctuation.
            chosen = max(scores, key=scores.get)
            if held is not None and scores.get(held, -1) >= -threshold:
                chosen = held
            elif scores[chosen] <= threshold:
                chosen = None
        else:
            chosen = None
        if held != chosen:
            if held is not None:
                cash += units * today[held]["open"] * (1 - COST)
                units, held, trades = 0.0, None, trades + 1
            if chosen is not None and cash > 0:
                units = cash * (1 - COST) / today[chosen]["open"]
                cash, held, trades = 0.0, chosen, trades + 1
        nav = cash + (units * today[held]["close"] if held else 0.0)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1 - nav / peak)
        equity.append(nav)
    if held:
        nav = cash + units * bars[-1]["prices"][held]["close"] * (1 - COST)
    else:
        nav = cash
    return {"final": round(nav, 6), "multiple": round(nav / starting, 6),
            "net_return": round(nav / starting - 1, 6),
            "max_drawdown": round(drawdown, 6), "trades": trades,
            "level_one": nav >= GOAL, "samples": len(equity)}


def assess(dataset):
    bars = dataset["bars"]
    pairs = tuple(dataset.get("pairs") or PAIRS)
    if len(bars) < 120:
        raise ValueError("Not enough chronological bars for independent test")
    if any(b["ts"] - a["ts"] != 3600 for a, b in zip(bars, bars[1:])):
        raise ValueError("Non-contiguous price history cannot be treated as hourly")
    if any(p not in bar["prices"] or bar["prices"][p]["open"] <= 0 or
           bar["prices"][p]["close"] <= 0 or
           not math.isfinite(bar["prices"][p]["open"]) or
           not math.isfinite(bar["prices"][p]["close"])
           for bar in bars for p in pairs):
        raise ValueError("Missing or invalid price bars")
    cut = int(len(bars) * 0.60)
    train, holdout = bars[:cut], bars[cut:]
    candidates = [(rule, period, threshold)
                  for rule in ("momentum", "reversal", "trend", "persistent")
                  for period in (3, 6, 12, 24, 48)
                  for threshold in (0.0, 0.005, 0.01, 0.03)]
    # Defensive cash is a genuine contender; a negative-edge period must not
    # force BEAN to buy a losing strategy merely to look active.
    candidates.insert(0, ("cash", 3, 0.0))
    ranked = [(run(train, *c, pairs=pairs), c) for c in candidates]
    # Optimize return penalized by realized drawdown, not number of trades.
    score = lambda r: math.log(max(1e-12, r["multiple"])) - r["max_drawdown"]
    best_train, best = max(ranked, key=lambda pair: score(pair[0]))
    challenger = run(holdout, *best, pairs=pairs)
    benchmark = run(holdout, "buy_hold", 3, 0, pairs=pairs)
    cash = run(holdout, "cash", 3, 0, pairs=pairs)
    return {"schema": "bean.coinbase.walkforward.v1",
            "historical_source": dataset["source"],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "training_bars": len(train), "holdout_bars": len(holdout),
            "pairs": dataset["pairs"], "starting_bankroll": START,
            "level_one_target": GOAL, "strategy": dict(zip(("rule","lookback","threshold"), best)),
            "training": best_train, "holdout": challenger,
            "buy_hold_btc": benchmark, "cash": cash,
            "beats_btc_holdout": challenger["final"] > benchmark["final"],
            "beats_cash_holdout": challenger["final"] > cash["final"],
            "level_one_achieved": challenger["level_one"],
            "note": "Historical replay is not live performance. Selection was only on training bars; holdout is untouched."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("artifacts/bean-historical.json"))
    ap.add_argument("--dataset", type=Path)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--discover-all", action="store_true", help="Discover all Coinbase USD/USDC assets; no allowlist")
    ap.add_argument("--catalog-only", action="store_true", help="Save full eligible Coinbase product manifest without downloading candles")
    ap.add_argument("--max-products", type=int, default=0, help="Optional API workload limit, 0 means no cap")
    args = ap.parse_args()
    if args.catalog_only:
        products = discover_products()
        result = {"schema": "bean.coinbase.catalog.v1", "source": "Coinbase Exchange public products",
                  "markets": products, "asset_count": len(products),
                  "qualification": "Online USD/USDC public products; account execution not verified"}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"asset_count": len(products), "contains_DOGE": "DOGE-USD" in products,
                          "contains_SHIB": "SHIB-USD" in products}))
        return
    if args.dataset:
        data = json.loads(args.dataset.read_text())
    else:
        end = int(time.time() // 3600 * 3600)
        pairs = discover_products() if args.discover_all else PAIRS
        if args.max_products > 0:
            pairs = pairs[:args.max_products]
        data = collect(end - args.days * 86400, end, pairs=pairs)
    result = assess(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
