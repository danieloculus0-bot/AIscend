"""Published systematic trading-rule families, implemented for Coinbase spot research.

These are independently specified adaptations, NOT claims to reproduce any
paper's exact portfolio, venue, fills or returns. Six-hour candle decisions
are made AFTER close and filled only at the next six-hour open.

Source families:
- Original Turtle rules: Donchian 20/10 and 55/20 day channels (close-confirmed).
- Moskowitz, Ooi & Pedersen (2012): own-return time-series momentum (spot).
- Svogun & Rudys (2024): moving average rules with nonzero confirmation bands.
- Deprez & Froemmel (2024): on-balance-volume technical-rule family.

No Coinbase credentials, wallet import, funding or order submission.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from urllib.parse import quote, urlencode

from .bean_six_hour_universe import BAR, MAX_CANDLES
from .bean_strategy_lab import _get_json

DEFAULT_FEE_BPS = 90.0
DEFAULT_SPREAD_BPS = 10.0
WARMUP = 240  # 60 complete days before any strategy decision
MIN_BARS = 700
RULES = (
    ("donchian_20_10",),
    ("donchian_55_20",),
    ("sma_band_10_30", 0.02),
    ("sma_band_10_30", 0.04),
    ("time_series_momentum_30d", 0.04),
    ("time_series_momentum_60d", 0.08),
    ("obv_trend",),
)
SOURCES = {
    "donchian": "Original Turtle Trading Rules, System 1 (20/10) and System 2 (55/20)",
    "sma_band": "Svogun and Rudys (2024), 10.1080/13504851.2024.2394211; parameterized variant",
    "time_series": "Moskowitz, Ooi and Pedersen (2012), 10.1016/j.jfineco.2011.11.003; unlevered spot variant",
    "obv": "Deprez and Froemmel (2024), 10.1016/j.iref.2024.05.003; independent OBV variant",
}


def normalize(raw):
    """Parse actual complete 6h OHLCV. Gaps and young listings are normal."""
    rows = [dict(v) for _, v in sorted(raw.items())]
    previous = None
    for row in rows:
        try:
            ts = int(row["ts"])
            op, high, low, cl, vol = (float(row[k]) for k in
                                       ("open", "high", "low", "close", "volume"))
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ValueError("PARTIAL") from None
        if (ts % BAR or (previous is not None and ts != previous + BAR)
                or not all(math.isfinite(x) for x in (op, high, low, cl, vol))
                or min(op, high, low, cl) <= 0 or vol < 0
                or high < max(op, low, cl) or low > min(op, cl, high)):
            raise ValueError("PARTIAL")
        previous = ts
    return rows


def fetch_public_ohlcv(product, *, days=270, clock=None, fetcher=_get_json):
    """Only Coinbase public Exchange 6h candles; no authenticated endpoint."""
    product = str(product).upper()
    base, sep, quote_unit = product.rpartition("-")
    if (not sep or not base or not base.replace("-", "").isalnum()
            or quote_unit not in {"USD", "USDC"}):
        raise ValueError("UNSUPPORTED_PRODUCT")
    if not 175 <= days <= 365:
        raise ValueError("DAYS_OUT_OF_RANGE")
    end = int((clock or time.time)() // BAR) * BAR
    start = end - days * 86400
    result = {}
    for chunk in range(start, end, MAX_CANDLES*BAR):
        stop = min(end, chunk + MAX_CANDLES*BAR)
        query = urlencode({
            "start": datetime.fromtimestamp(chunk, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(stop, timezone.utc).isoformat(),
            "granularity": BAR,
        })
        url = (f"https://api.exchange.coinbase.com/products/"
               f"{quote(product, safe='-')}/candles?{query}")
        payload = fetcher(url)
        if not isinstance(payload, list):
            raise ValueError("PARTIAL")
        for candle in payload:
            if not isinstance(candle, (list, tuple)) or len(candle) < 6:
                continue
            try:
                ts = int(candle[0])
                point = dict(ts=ts, low=float(candle[1]), high=float(candle[2]),
                             open=float(candle[3]), close=float(candle[4]),
                             volume=float(candle[5]))
            except (ValueError, TypeError, OverflowError):
                continue
            if chunk <= ts < stop and ts % BAR == 0:
                if ts in result and result[ts] != point:
                    raise ValueError("PARTIAL")
                result[ts] = point
    return result


def _mean(rows, i, n):
    return sum(float(r["close"]) for r in rows[i-n+1:i+1])/n


def _signal(rows, i, rule):
    """Entry and exit instructions using no bar newer than completed i."""
    if i < WARMUP-1:
        return False, False
    name, *params = rule
    close = rows[i]["close"]
    if name == "donchian_20_10":
        entry_days, exit_days = 80, 40
    elif name == "donchian_55_20":
        entry_days, exit_days = 220, 80
    else:
        entry_days = exit_days = 0
    if entry_days:
        entry = close > max(r["high"] for r in rows[i-entry_days:i])
        exit_now = close < min(r["low"] for r in rows[i-exit_days:i])
    elif name == "sma_band_10_30":
        band = params[0]
        fast, slow = _mean(rows, i, 40), _mean(rows, i, 120)
        entry = fast > slow*(1+band)
        exit_now = fast < slow*(1-band)
    elif name.startswith("time_series_momentum_"):
        span = 120 if name.endswith("30d") else 240
        gain = close/rows[i-span]["close"]-1
        entry, exit_now = gain > params[0], gain < 0
    elif name == "obv_trend":
        # Signed traded volume is On Balance Volume's increment.
        obv = sum((1 if rows[k]["close"] > rows[k-1]["close"]
                   else -1 if rows[k]["close"] < rows[k-1]["close"] else 0)
                  * rows[k]["volume"] for k in range(i-55, i+1))
        entry = (obv > 0 and close > _mean(rows, i, 120)
                 and close > rows[i-56]["close"])
        exit_now = obv < 0 or close < _mean(rows, i, 40)
    else:
        raise ValueError("UNKNOWN_RULE")
    return bool(entry), bool(exit_now)


def simulate(rows, rule, *, start, end, fee_bps=DEFAULT_FEE_BPS,
             spread_bps=DEFAULT_SPREAD_BPS):
    if not WARMUP <= start < end <= len(rows):
        raise ValueError("INVALID_WINDOW")
    cost = (float(fee_bps)+float(spread_bps)/2)/10000
    if not math.isfinite(cost) or not 0 <= cost < 1:
        raise ValueError("INVALID_COST")
    cash, units, cost_at_entry = 1., 0., 0.
    roundtrips = wins = fills = 0
    peak, dd = 1., 0.
    for i in range(start, end):
        enter, exit_now = _signal(rows, i-1, rule)
        opening = rows[i]["open"]
        if units and exit_now:
            cash = units*opening*(1-cost)
            roundtrips += 1
            wins += int(cash > cost_at_entry)
            fills += 1
            units = 0.
        elif not units and enter:
            cost_at_entry = cash
            units = cash*(1-cost)/opening
            cash = 0.
            fills += 1
        liquidatable = (cash if not units else units*rows[i]["close"]*(1-cost))
        peak = max(peak, liquidatable)
        dd = max(dd, 1-liquidatable/peak)
    nav = cash if not units else units*rows[end-1]["close"]*(1-cost)
    return {"net_return": round(nav-1, 8), "max_drawdown": round(dd, 8),
            "roundtrips": roundtrips, "wins": wins, "orders": fills,
            "open_position": bool(units)}


def _passive(rows, start, end, cost):
    return rows[end-1]["close"]/rows[start]["open"]*(1-cost)**2-1


def evaluate(raw, *, fee_bps=DEFAULT_FEE_BPS, spread_bps=DEFAULT_SPREAD_BPS,
             stress_fee_bps=180., stress_spread_bps=30.):
    """Training-only selection; untouched historical holdout; no live adoption."""
    try:
        rows = normalize(raw)
    except ValueError as exc:
        if str(exc) == "PARTIAL":
            return {"status": "PARTIAL", "promotion_authorized": False}
        raise
    if len(rows) < MIN_BARS:
        return {"status": "INSUFFICIENT_HISTORY", "bars": len(rows),
                "promotion_authorized": False}
    split = int(len(rows)*.70)
    train = []
    for rule in RULES:
        result = simulate(rows, rule, start=WARMUP, end=split,
                          fee_bps=fee_bps, spread_bps=spread_bps)
        train.append({"rule": list(rule), **result})
    # At least two finished trades before considering a training winner.
    viable = [r for r in train if r["roundtrips"] >= 2]
    winner = max(viable, key=lambda row: (
        row["net_return"]-.4*row["max_drawdown"], row["net_return"],
        -RULES.index(tuple(row["rule"])))) if viable else None
    cost = (fee_bps+spread_bps/2)/10000
    report = {"status": "OOS_NOT_PROVEN", "bars": len(rows),
              "training_end": rows[split-1]["ts"],
              "holdout_start": rows[split]["ts"],
              "fee_bps": fee_bps, "spread_bps": spread_bps,
              "sources": SOURCES, "training": train,
              "selected_rule": winner["rule"] if winner else None,
              "holdout": None, "stress": None,
              "buy_hold_net": round(_passive(rows, split, len(rows), cost), 8),
              "promotion_authorized": False,
              "note": "Published rule families, independently adapted. "
                      "Historical holdout is not forward profitability."}
    if winner is None:
        return report
    rule = tuple(winner["rule"])
    holdout = simulate(rows, rule, start=split, end=len(rows),
                       fee_bps=fee_bps, spread_bps=spread_bps)
    stress = simulate(rows, rule, start=split, end=len(rows),
                      fee_bps=stress_fee_bps, spread_bps=stress_spread_bps)
    report["holdout"], report["stress"] = holdout, stress
    if (holdout["roundtrips"] >= 2 and holdout["net_return"] >
            max(0., report["buy_hold_net"]) and stress["net_return"] > 0):
        report["status"] = "OOS_PASS"
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--product", action="append", required=True)
    p.add_argument("--days", type=int, default=270)
    p.add_argument("--output", type=Path,
                   default=Path("artifacts/published-strategy-research.json"))
    args = p.parse_args(argv)
    fixed_clock = int(time.time()//BAR)*BAR
    reports = {}
    for product in sorted(set(p.upper() for p in args.product)):
        try:
            data = fetch_public_ohlcv(product, days=args.days,
                                      clock=lambda: fixed_clock)
            reports[product] = evaluate(data)
        except Exception as exc:
            reports[product] = {"status": "DATA_UNAVAILABLE",
                                "reason": type(exc).__name__,
                                "promotion_authorized": False}
    result = {"schema": "aiscend.published.strategy.research.v1",
              "as_of": fixed_clock, "research_only": True,
              "promotion_authorized": False, "markets": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True,
                                      indent=2, allow_nan=False)+"\n",
                           encoding="utf-8")
    print(json.dumps({k: v["status"] for k, v in reports.items()},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
