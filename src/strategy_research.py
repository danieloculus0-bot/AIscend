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


def simulate(bars: dict, policy: str, *, fee_bps=60.0, spread_bps=10.0,
             start=0, end=None) -> dict:
    times=sorted(bars)
    n=len(times)
    end=n if end is None else min(n,int(end))
    last=max(73,int(start))
    trades=[]
    equity=1.0
    equity_peak=1.0
    worst_dd=0.0
    while last < end-2:
        i=last
        if any(times[k+1]-times[k]!=BAR for k in range(i-72,i+1)):
            last+=1
            continue
        closes=[float(bars[times[k]]["close"]) for k in (i,i-1,i-12,i-72)]
        price,prior,p1,p6=closes
        if min(closes)<=0:
            last+=1
            continue
        row={"product":"TEST-USD", "return_5m":price/prior-1,
             "return_1h":price/p1-1,
             "return_6h":price/p6-1,
             "volume_ratio":1.5,"spread_bps":spread_bps}
        signal=next((s for s in proposals(row,enabled=(policy,))),None)
        if not signal:
            last+=1
            continue
        h=int(signal["maturity_minutes"]//5)
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
        net=(sell/buy)*(1-(fee_bps+spread_bps/2)/10000)**2-1
        equity*=1+net
        equity_peak=max(equity_peak,equity)
        worst_dd=max(worst_dd,1-equity/equity_peak)
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
           for p in POLICIES}
    # Select ONLY using chronological training. No holdout lookahead.
    champion=max(POLICIES,key=lambda p: (
        train[p]["net_return"] if train[p]["trades"]>=3 else -math.inf))
    test={p: simulate(bars,p,fee_bps=fee_bps,
                      spread_bps=spread_bps,start=split,end=n)
          for p in POLICIES}
    first=float(bars[times[split]]["open"])
    last=float(bars[times[-1]]["close"])
    passive=(last/first)*(1-(fee_bps+spread_bps/2)/10000)**2-1
    winner=test[champion]
    qualified=(train[champion]["trades"]>=3 and winner["trades"]>=3
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
