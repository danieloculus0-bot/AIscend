"""Research-only, causal, fee-net opportunity estimator for six-hour Coinbase bars.

This is an independent candidate study, NOT a change to any frozen BEAN policy.
Uses non-overlapping matured historical examples to decide when abstention beats
paying the fee. No credentials, orders, live activation or promotion.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .bean_six_hour_universe import BAR, fetch_history

WARMUP = 21
DEFAULT_HOLD_BARS = 8
DEFAULT_FEE_BPS = 90.0
DEFAULT_SPREAD_BPS = 10.0


def normalize(raw):
    rows = sorted((dict(v) for v in raw.values()), key=lambda row: row["ts"])
    if not rows:
        raise ValueError("INSUFFICIENT_HISTORY")
    last = None
    for row in rows:
        ts = int(row["ts"])
        try:
            o, c, v = (float(row[k]) for k in ("open", "close", "volume"))
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ValueError("INVALID_CANDLE") from exc
        if (ts % BAR or (last is not None and ts != last + BAR) or
                not all(math.isfinite(x) for x in (o, c, v)) or
                min(o, c) <= 0 or v < 0):
            raise ValueError("INVALID_OR_MISSING_CANDLE")
        row.update(ts=ts, open=o, close=c, volume=v)
        last = ts
    return rows


def context(rows, index):
    """Only 16 previously completed prices; no asset identifiers or future rows."""
    if index < WARMUP:
        raise ValueError("INSUFFICIENT_FEATURE_HISTORY")
    close = rows[index]["close"]
    four = close / rows[index - 4]["close"] - 1
    sixteen = close / rows[index - 16]["close"] - 1
    trend = ("up" if four > .005 and sixteen > .01 else
             "down" if four < -.005 and sixteen < -.01 else "mixed")
    returns = [math.log(rows[j]["close"] / rows[j-1]["close"])
               for j in range(index-11, index+1)]
    sigma = math.sqrt(sum(x*x for x in returns) / len(returns))
    return trend + ("/high_vol" if sigma >= .02 else "/low_vol")


def side_cost(fee_bps=DEFAULT_FEE_BPS, spread_bps=DEFAULT_SPREAD_BPS):
    if (not all(math.isfinite(float(x)) for x in (fee_bps, spread_bps))
            or fee_bps < 0 or spread_bps < 0 or fee_bps + spread_bps/2 >= 10000):
        raise ValueError("INVALID_COST")
    return (fee_bps + spread_bps/2) / 10000


def matured_samples(rows, decision_index, *, hold_bars=DEFAULT_HOLD_BARS,
                    lookback_bars=240, fee_bps=DEFAULT_FEE_BPS,
                    spread_bps=DEFAULT_SPREAD_BPS):
    """Outcomes must be observable BEFORE decision close; non-overlapping labels.

    Signal j follows close j. Entry open j+2 and exit open j+hold+2.
    Decision i can use an outcome only if j+hold+2 <= i. The global WARMUP
    anchor avoids overlapping holding windows falsely increasing sample size.
    """
    if hold_bars < 1 or lookback_bars < WARMUP or decision_index >= len(rows):
        raise ValueError("INVALID_WINDOW")
    cost = side_cost(fee_bps, spread_bps)
    first = max(WARMUP, decision_index-lookback_bars)
    stride = hold_bars+1
    first += (WARMUP-first) % stride
    samples = {}
    for j in range(first, decision_index-hold_bars-1, stride):
        bucket = context(rows, j)
        entry, exit_open = rows[j+2]["open"], rows[j+hold_bars+2]["open"]
        net = exit_open/entry*(1-cost)**2-1
        samples.setdefault(bucket, []).append(net)
    return samples


def forecast(rows, index, *, hold_bars=DEFAULT_HOLD_BARS, min_samples=6,
             min_edge_bps=50, confidence_z=1.645, lookback_bars=240,
             fee_bps=DEFAULT_FEE_BPS, spread_bps=DEFAULT_SPREAD_BPS):
    """One-sided lower-confidence estimate of fee-net return, or abstain."""
    if (min_samples < 2 or min_edge_bps < 0 or
            not math.isfinite(confidence_z) or confidence_z < 0):
        raise ValueError("INVALID_GATE")
    label = context(rows, index)
    samples = matured_samples(rows, index, hold_bars=hold_bars,
                              lookback_bars=lookback_bars,
                              fee_bps=fee_bps, spread_bps=spread_bps).get(label, [])
    n = len(samples)
    mean = sum(samples)/n if n else None
    if n >= 2:
        variance = sum((x-mean)**2 for x in samples)/(n-1)
        lower = mean - confidence_z*math.sqrt(variance/n)
    else:
        lower = None
    action = bool(n >= min_samples and lower is not None and
                  lower > min_edge_bps/10000)
    return {"decision_ts": rows[index]["ts"]+BAR, "context": label,
            "observed_training_through_ts": rows[index]["ts"]+BAR,
            "samples": n, "mean_fee_net": mean, "lower_fee_net": lower,
            "fee_net_win_frequency": (sum(x > 0 for x in samples)/n if n else None),
            "decision": "PAPER_LONG" if action else "CASH",
            "reason": ("EDGE_LCB_ABOVE_COST" if action else
                       "INSUFFICIENT_MATURED_EXAMPLES" if n < min_samples else
                       "EDGE_NOT_ESTABLISHED")}


def experiment(raw, *, hold_bars=DEFAULT_HOLD_BARS, fee_bps=DEFAULT_FEE_BPS,
               spread_bps=DEFAULT_SPREAD_BPS, min_samples=6,
               min_edge_bps=50, confidence_z=1.645, lookback_bars=240,
               split=.65):
    """Chronological online development replay; does NOT reopen a sealed test."""
    rows = normalize(raw)
    side = side_cost(fee_bps, spread_bps)
    if not .5 <= split <= .8 or len(rows) < 180:
        raise ValueError("INSUFFICIENT_HISTORY")
    if hold_bars < 1 or hold_bars >= 30:
        raise ValueError("INVALID_HOLD")
    start, end = int(len(rows)*split), len(rows)
    if start <= WARMUP + hold_bars or end-start <= hold_bars+2:
        raise ValueError("INSUFFICIENT_HISTORY")
    cash, quantity, pending, held_until = 1., 0., None, None
    peak, max_dd, trades, wins, abstentions = 1., 0., [], 0, 0
    decisions = []
    for i in range(start, end):
        row = rows[i]
        if quantity and i == held_until:
            proceeds = quantity*row["open"]*(1-side)
            record = trades[-1]
            record["exit_ts"] = row["ts"]
            record["net_return"] = proceeds/record["entry_equity"]-1
            wins += int(proceeds > record["entry_equity"])
            cash, quantity, held_until = proceeds, 0., None
        if pending is not None and i == pending["entry_index"]:
            quantity = cash*(1-side)/row["open"]
            trades.append({"decision_ts": pending["decision_ts"],
                           "entry_ts": row["ts"], "entry_equity": cash,
                           "context": pending["context"],
                           "sample_count": pending["samples"],
                           "predicted_lower_fee_net": pending["lower_fee_net"]})
            cash, held_until, pending = 0., pending["exit_index"], None
        nav = cash if not quantity else quantity*row["close"]*(1-side)
        peak = max(peak, nav)
        max_dd = max(max_dd, 1-nav/peak)
        if quantity or pending or i > end-hold_bars-3:
            continue
        decision = forecast(rows, i, hold_bars=hold_bars,
                            fee_bps=fee_bps, spread_bps=spread_bps,
                            min_samples=min_samples,
                            min_edge_bps=min_edge_bps,
                            confidence_z=confidence_z,
                            lookback_bars=lookback_bars)
        decisions.append(decision)
        if decision["decision"] == "PAPER_LONG":
            pending = {**decision, "entry_index": i+2,
                       "exit_index": i+hold_bars+2}
        else:
            abstentions += 1
    if quantity or pending or any("exit_ts" not in t for t in trades):
        raise AssertionError("An unfinished paper position escaped the evaluation boundary")
    baseline = rows[end-1]["close"]/rows[start+2]["open"]*(1-side)**2-1
    net = cash-1
    return {"schema": "aiscend.bean.edge_lab.v1",
            "study_type": "REUSED_HISTORICAL_DEVELOPMENT_NOT_FRESH_OOS",
            "start_ts": rows[start]["ts"], "end_ts": rows[end-1]["ts"]+BAR,
            "bars": end-start, "hold_bars": hold_bars,
            "cost_bps_per_side": fee_bps+spread_bps/2,
            "breakeven_gross_roundtrip_pct": 100*((1/(1-side)**2)-1),
            "min_samples": min_samples, "min_edge_bps": min_edge_bps,
            "lookback_bars": lookback_bars,
            "signals_checked": len(decisions), "abstentions": abstentions,
            "completed_roundtrips": len(trades), "wins": wins,
            "fee_net_return": net, "cash_return": 0.,
            "passive_fee_net_return": baseline,
            "excess_vs_passive": net-baseline, "max_drawdown": max_dd,
            "decisions": decisions, "hypothetical_trades": trades,
            "promotion_authorized": False, "live_orders": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--product", required=True)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    if not 45 <= args.days <= 120:
        p.error("Days must be 45-120")
    from time import time
    fixed_now = int(time()//BAR)*BAR
    history = fetch_history(args.product.upper(), days=args.days,
                            clock=lambda: fixed_now)
    report = experiment(history)
    report["product"] = args.product.upper()
    report["data_end_exclusive"] = fixed_now
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2, sort_keys=True, allow_nan=False)
        out.write("\n")
    print(json.dumps({k: report[k] for k in ("product", "fee_net_return",
          "passive_fee_net_return", "completed_roundtrips", "promotion_authorized")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
