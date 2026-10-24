"""Fee-net, next-open, multi-day Coinbase strategy discovery. Research only.

A training-only winner is scored on an untouched chronological historical
holdout. No wallet, credentials, order submission or automatic promotion.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

BAR = 21600
WARMUP = 40
FEE_BPS = 90.0
SPREAD_BPS = 10.0
CONFIGS = tuple((family, lookback, hold)
    for family in ("breakout", "relative_momentum", "pullback")
    for lookback in (20, 28)
    for hold in (16, 32))


def candles(raw):
    rows = [dict(v) for _, v in sorted(raw.items())]
    previous = None
    for r in rows:
        ts = int(r["ts"])
        if ts % BAR or (previous is not None and ts != previous + BAR):
            raise ValueError("PARTIAL_HISTORY")
        values = [float(r[k]) for k in ("open", "close", "volume")]
        if (not all(math.isfinite(x) for x in values)
                or min(values[:2]) <= 0 or values[2] < 0):
            raise ValueError("INVALID_CANDLE")
        previous = ts
    return rows


def avg(rows, first, stop, field="close"):
    return sum(float(r[field]) for r in rows[first:stop]) / (stop-first)


def signal(rows, btc, i, config):
    """Return enter/exit using observations through completed candle i."""
    family, lookback, _hold = config
    if i < max(WARMUP-1, lookback+1):
        return False, False
    close = float(rows[i]["close"])
    prior = float(rows[i-1]["close"])
    fast = avg(rows, i-7, i+1)
    slow = avg(rows, i-lookback+1, i+1)
    if family == "breakout":
        high = max(r["close"] for r in rows[i-lookback:i])
        v = avg(rows, i-lookback, i, "volume")
        entry = close > high and v > 0 and rows[i]["volume"] >= 1.25*v
        exit_now = close < fast or close < high * .94
    elif family == "relative_momentum":
        if btc is None:
            return False, False
        asset = close / rows[i-lookback]["close"] - 1
        reference = btc[i]["close"] / btc[i-lookback]["close"] - 1
        entry = (asset > max(.06, reference + .04)
                 and close > slow and reference > -.06)
        exit_now = close < fast or asset < reference
    elif family == "pullback":
        prior_fast = avg(rows, i-8, i)
        entry = (fast > slow and close > rows[i-lookback]["close"]*1.03
                 and prior <= prior_fast and close > fast)
        exit_now = close < slow or fast < slow
    else:
        raise ValueError("INVALID_STRATEGY")
    return bool(entry), bool(exit_now)


def simulate(rows, config, *, btc=None, start, end, fee_bps=FEE_BPS,
             spread_bps=SPREAD_BPS):
    """Signal at preceding close, hypothetical fill at next open only."""
    cost = (fee_bps + spread_bps/2)/10000
    if not math.isfinite(cost) or not 0 <= cost < 1:
        raise ValueError("INVALID_COST")
    if not WARMUP <= start < end <= len(rows):
        raise ValueError("INVALID_WINDOW")
    if btc is not None and (len(btc) != len(rows) or any(
            x["ts"] != y["ts"] for x, y in zip(rows, btc))):
        raise ValueError("UNALIGNED_BTC")
    cash, units, entry_cash, entry_i = 1.0, 0.0, 0.0, -1
    trips = wins = exposure = 0
    peak, drawdown = 1.0, 0.0
    for i in range(start, end):
        enter, exit_now = signal(rows, btc, i-1, config)
        opening = rows[i]["open"]
        if units and (exit_now or i-entry_i >= config[2]):
            cash = units * opening * (1-cost)
            trips += 1
            wins += int(cash > entry_cash)
            units = 0.0
        elif not units and enter:
            entry_cash, entry_i = cash, i
            units = cash * (1-cost)/opening
            cash = 0.0
        exposure += bool(units)
        # Mark-to-liquidation with both legs' modeled costs; not an actual fill.
        nav = cash if not units else units*rows[i]["close"]*(1-cost)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1-nav/peak)
    nav = cash if not units else units*rows[end-1]["close"]*(1-cost)
    return {"net_return": round(nav-1, 8), "max_drawdown": round(drawdown, 8),
            "completed_roundtrips": trips, "wins": wins,
            "open_position": bool(units), "exposure_fraction": round(exposure/(end-start), 6)}


def passive(rows, first, end, fee_bps=FEE_BPS, spread_bps=SPREAD_BPS):
    cost = (fee_bps+spread_bps/2)/10000
    return (rows[end-1]["close"]/rows[first]["open"])*(1-cost)**2-1


def challenge(raw, *, btc_raw=None, fee_bps=FEE_BPS,
              spread_bps=SPREAD_BPS, stress_fee_bps=180.,
              stress_spread_bps=30.):
    try:
        rows = candles(raw)
    except ValueError as e:
        if str(e) in {"PARTIAL_HISTORY", "INVALID_CANDLE"}:
            return {"status": "PARTIAL", "reason": str(e),
                    "promotion_authorized": False}
        raise
    # A BTC-data outage must not suppress independent breakout/pullback trials.
    try:
        btc = candles(btc_raw) if btc_raw is not None else None
    except (ValueError, KeyError, TypeError):
        btc = None
    if btc is not None and (len(btc) != len(rows) or any(
            x["ts"] != y["ts"] for x, y in zip(rows, btc))):
        btc = None
    if len(rows) < 240:
        return {"status": "INSUFFICIENT_HISTORY", "bars": len(rows),
                "promotion_authorized": False}
    split = int(len(rows)*.65)
    training = []
    for config in CONFIGS:
        if config[0] == "relative_momentum" and btc is None:
            continue
        trial = simulate(rows, config, btc=btc, start=WARMUP, end=split,
                         fee_bps=fee_bps, spread_bps=spread_bps)
        training.append({"config": list(config), **trial})
    viable = [r for r in training if r["completed_roundtrips"] >= 2]
    # Configurations and score are frozen before ever opening holdout outcomes.
    best = max(viable, key=lambda r: (
        r["net_return"]-.35*r["max_drawdown"], r["net_return"],
        -CONFIGS.index(tuple(r["config"])))) if viable else None
    report = {"status": "OOS_NOT_PROVEN", "bars": len(rows),
              "training_end_ts": rows[split-1]["ts"],
              "holdout_start_ts": rows[split]["ts"],
              "fee_bps": fee_bps, "spread_bps": spread_bps,
              "training_candidates": training,
              "selected_config": best["config"] if best else None,
              "holdout": None, "stress_holdout": None,
              "holdout_buy_hold_net": round(passive(rows, split, len(rows), fee_bps,
                                                       spread_bps), 8),
              "holdout_btc_hold_net": (round(passive(btc, split, len(rows), fee_bps,
                                                        spread_bps), 8)
                                       if btc is not None else None),
              "promotion_authorized": False,
              "note": "Opened historical holdout is research, not verified future profit."}
    if best is None:
        return report
    config = tuple(best["config"])
    oos = simulate(rows, config, btc=btc, start=split, end=len(rows),
                   fee_bps=fee_bps, spread_bps=spread_bps)
    stress = simulate(rows, config, btc=btc, start=split, end=len(rows),
                      fee_bps=stress_fee_bps, spread_bps=stress_spread_bps)
    report.update(holdout=oos, stress_holdout=stress)
    benchmark = max(0., report["holdout_buy_hold_net"],
                    report["holdout_btc_hold_net"] or 0.)
    if (oos["completed_roundtrips"] >= 2 and oos["net_return"] > benchmark
            and stress["net_return"] > 0):
        report["status"] = "OOS_PASS"
    return report


def main(argv=None):
    from .bean_six_hour_universe import fetch_history
    import time
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--product", action="append", required=True,
                   help="Repeat for Coinbase USD/USDC pairs")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--output", type=Path,
                   default=Path("artifacts/strategy-discovery.json"))
    args = p.parse_args(argv)
    if not 45 <= args.days <= 120:
        p.error("Days must be between 45 and 120")
    reports = {}
    btc = None
    fixed_clock = int(time.time()//BAR)*BAR
    for product in sorted(set(p.upper() for p in args.product)):
        try:
            bars = fetch_history(product, days=args.days, clock=lambda: fixed_clock)
            if product != "BTC-USD" and btc is None:
                btc = fetch_history("BTC-USD", days=args.days, clock=lambda: fixed_clock)
            reports[product] = challenge(bars,
                btc_raw=btc if product != "BTC-USD" else None)
        except Exception as exc:
            reports[product] = {"status": "DATA_UNAVAILABLE",
                                "reason": type(exc).__name__,
                                "promotion_authorized": False}
    output = {"schema": "aiscend.strategy.discovery.v1",
              "period_seconds": BAR, "products": reports,
              "promotion_authorized": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True,
                                      allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"products": {p: x["status"] for p,x in reports.items()},
                      "output": str(args.output), "promotion_authorized": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
