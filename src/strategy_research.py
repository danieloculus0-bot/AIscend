"""Causal, fee-inclusive walk-forward evaluation for AIscend strategies.

Real public Coinbase five-minute candles only. Train selection is chronological;
the final holdout is not used to tune parameters. No fake fills or account API.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from .bean_five_minute import BAR, fetch_5m
from .primary_strategy import POLICIES, proposals

RESEARCH_POLICIES = (*POLICIES, "trend_4h_hold", "trend_1d_hold", "cash")


def simulate(bars: dict, policy: str, *, fee_bps=60.0, spread_bps=10.0,
             start=0, end=None) -> dict:
    times=sorted(bars)
    n=len(times)
    end=n if end is None else min(n,int(end))
    if policy == "cash":
        return {"policy":policy,"trades":0,"wins":0,"win_rate":None,
                "net_return":0.0,"max_drawdown":0.0,"trade_examples":[],
                "method":"no_position_no_fees"}
    last=max(73,int(start))
    trades=[]
    equity=1.0
    equity_peak=1.0
    worst_dd=0.0
    cost=(fee_bps+spread_bps/2)/10000
    if not 0 <= cost < 1:
        raise ValueError("Invalid modeled per-side execution cost")

    def record_mark(mark):
        nonlocal equity_peak, worst_dd
        equity_peak=max(equity_peak,mark)
        worst_dd=max(worst_dd,1-mark/equity_peak)

    while last < end-2:
        i=last
        lookback=145 if policy in ("trend_4h_hold","trend_1d_hold") else 72
        if i < lookback or any(times[k+1]-times[k]!=BAR
                               for k in range(i-lookback,i+1)):
            last+=1
            continue
        closes=[float(bars[times[k]]["close"]) for k in (i,i-1,i-12,i-72)]
        price,prior,p1,p6=closes
        if min(closes)<=0:
            last+=1
            continue
        # Use only this completed candle and twelve preceding completed
        # candles for volume confirmation. Missing volume is NOT 1.5x.
        try:
            volumes=[float(bars[times[k]]["volume"]) for k in range(i-12,i+1)]
            trailing_mean=sum(volumes[:-1])/12
            volume_ratio=(volumes[-1]/trailing_mean
                          if all(math.isfinite(v) and v>=0 for v in volumes)
                          and trailing_mean>0 else 0.0)
        except (KeyError, TypeError, ValueError, OverflowError):
            volume_ratio=0.0
        row={"product":"TEST-USD", "return_5m":price/prior-1,
             "return_1h":price/p1-1,
             "return_6h":price/p6-1,
             "volume_ratio":volume_ratio,"spread_bps":spread_bps}
        if policy in ("trend_4h_hold","trend_1d_hold"):
            fast_1h=sum(float(bars[times[k]]["close"])
                         for k in range(i-11,i+1))/12
            fast_4h=sum(float(bars[times[k]]["close"])
                         for k in range(i-47,i+1))/48
            slow_12h=sum(float(bars[times[k]]["close"])
                         for k in range(i-143,i+1))/144
            eligible=(fast_1h>fast_4h and price>fast_1h) if policy=="trend_4h_hold" else (
                fast_4h>slow_12h and price>fast_4h)
            h=48 if policy=="trend_4h_hold" else 288
        else:
            signal=next((s for s in proposals(row,enabled=(policy,))),None)
            eligible=signal is not None
            h=int(signal["maturity_minutes"]//5) if signal else 0
        if not eligible:
            last+=1
            continue
        exit_i=i+1+h
        if exit_i>=end:
            last+=1
            continue
        # The completed bar i is the LAST input. Orders fill hypothetically
        # at future i+1 and exit_i OPENs, never at signal-bar closes.
        buy=float(bars[times[i+1]]["open"])
        sell=float(bars[times[exit_i]]["open"])
        if buy<=0 or sell<=0:
            last+=1
            continue
        # Include liquidation-cost marks throughout the open position.
        # Exit-only drawdowns conceal severe interim capital impairment.
        record_mark(equity*(1-cost))
        for k in range(i+1,exit_i):
            mark=float(bars[times[k]]["close"])
            if not math.isfinite(mark) or mark<=0:
                raise ValueError("Invalid held-position mark in historical candles")
            record_mark(equity*(1-cost)**2*mark/buy)
        net=(sell/buy)*(1-cost)**2-1
        equity*=1+net
        record_mark(equity)
        trades.append({"entry_ts":times[i+1],"exit_ts":times[exit_i],
                       "net_return":round(net,8),
                       "win":bool(net>0)})
        last=exit_i
    sample=len(trades)
    return {"policy":policy,"trades":sample,
            "wins":sum(t["win"] for t in trades),
            "win_rate":sum(t["win"] for t in trades)/sample if sample else None,
            "net_return":round(equity-1,8),
            "max_drawdown":round(worst_dd,8),
            "trade_examples":trades[:5],
            "method":"next_completed_bar_open_with_full_taker_and_spread_costs"}


def validate(bars: dict, *, fee_bps=60.0, spread_bps=10.0,
             training_fraction=.70) -> dict:
    times=sorted(bars)
    n=len(times)
    if n<800:
        return {"status":"INSUFFICIENT_HISTORY","bars":n}
    split=int(n*training_fraction)
    train={p: simulate(bars,p,fee_bps=fee_bps,
                       spread_bps=spread_bps,start=0,end=split)
           for p in RESEARCH_POLICIES}
    # Select ONLY using chronological training. No holdout lookahead.
    champion=max(RESEARCH_POLICIES,key=lambda p: (
        train[p]["net_return"] if p=="cash" or train[p]["trades"]>=3 else -math.inf))
    test={p: simulate(bars,p,fee_bps=fee_bps,
                      spread_bps=spread_bps,start=split,end=n)
          for p in RESEARCH_POLICIES}
    first=float(bars[times[split]]["open"])
    last=float(bars[times[-1]]["close"])
    passive=(last/first)*(1-(fee_bps+spread_bps/2)/10000)**2-1
    winner=test[champion]
    qualified=(champion!="cash" and train[champion]["trades"]>=3 and winner["trades"]>=3
               and winner["net_return"]>0 and winner["net_return"]>passive)
    return {"status":"OOS_PASS" if qualified else "OOS_NOT_PROVEN",
            "bars":n,"split_timestamp":times[split],
            "selection_rule":"maximum_training_net_return_nonoverlapping",
            "champion":champion,"training":train,
            "holdout":test,
            "holdout_buy_hold_net":round(passive,8),
            "promotion_eligible":qualified,
            "note":"Not a proof of future profit; no automatic live promotion"}


def historical_bars(product: str, *, days=14, clock=None, fetcher=fetch_5m) -> dict:
    end=int((clock or time.time)()//BAR)*BAR
    start=end-int(days*86400)
    out={}
    for t in range(start,end,288*BAR):
        part=fetcher(product,t,min(t+288*BAR,end))
        out.update(part)
    return out


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--products",nargs="+",default=[
        "BTC-USD","ETH-USD","SOL-USD","ADA-USD","DOGE-USD"])
    parser.add_argument("--days",type=int,default=21)
    parser.add_argument("--fee-bps",type=float,default=60.0)
    parser.add_argument("--output",default="strategy_evidence.json")
    a=parser.parse_args()
    reports={}
    for product in a.products:
        try:
            bars=historical_bars(product,days=a.days)
            reports[product]=validate(bars,fee_bps=a.fee_bps)
        except Exception as exc:
            reports[product]={"status":"DATA_UNAVAILABLE",
                              "reason":f"{type(exc).__name__}: {str(exc)[:160]}"}
    report={"generated_at":time.time(),"source":"public_coinbase_historical_bars",
            "fee_bps":a.fee_bps,"days":a.days,"products":reports}
    Path(a.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({p:{"status":r["status"],
                          "champion":r.get("champion"),
                          "holdout_return":(r.get("holdout") or {}).get(
                              r.get("champion"),{}).get("net_return")}
                      for p,r in reports.items()},indent=2))


if __name__=="__main__":
    main()
