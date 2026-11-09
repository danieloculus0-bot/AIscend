"""AIscend research ONLY: causal cross-asset rotation with the REAL pinned BEAN SpongeDQN.

Three economically distinct targets (cash, strongest, runner-up) allow HOLD,
ROTATE, DEFEND, and REENTER to emerge from positions and price history.
This module NEVER imports an AIscend execution adapter or opens exchange connections.

Research note: a synthetic fixture tests machinery, NOT market profitability.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
import subprocess
import sys
from pathlib import Path

BAR = 21600  # Six-hour candles
HORIZON = 8
DELAY = 2  # Decision follows close t; fill starts at open t+2, not t+1.
LOOKBACK = 16
COST = 0.0095  # 95 basis points PER SIDE (fee + conservative spread/slippage).
PARTICIPATION = 0.01
ACTIONS = ("cash", "leader", "runner_up")
CORE_REVISION = "d13ea68fd2d0fd4cd7af1cd297e1fbec75a954fe"
CORE_BLOB = "08abcf5039f7f440fa936aba31ef9a3a2b96f153"  # sponge_dqn.py at pinned Core.
SCHEMA = "aiscend.bean.shadow.rotation.research.v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def load_bars(rows):
    """Strict aligned OHCV (plus optional H/L), exactly two public assets."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("EMPTY_SOURCE")
    by_symbol = {}
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("INVALID_BAR")
        timestamp = r.get("open_time", r.get("ts"))
        if type(timestamp) is not int or ("open_time" in r and "ts" in r and r["open_time"] != r["ts"]):
            raise ValueError("INVALID_TIMESTAMP")
        symbol = r.get("symbol")
        if not isinstance(symbol, str) or not symbol or len(symbol) > 30:
            raise ValueError("INVALID_SYMBOL")
        values = []
        for key in ("open", "close", "volume"):
            v = r.get(key)
            if type(v) not in (int, float) or not math.isfinite(v):
                raise ValueError("NONFINITE_BAR")
            values.append(float(v))
        o,c,v = values
        if timestamp % BAR or min(o,c,v) <= 0:
            raise ValueError("INVALID_BAR")
        h=l=None
        if "high" in r or "low" in r:
            if not ("high" in r and "low" in r):
                raise ValueError("INCOMPLETE_RANGE")
            h,l = r["high"],r["low"]
            if (type(h) not in (int,float) or type(l) not in (int,float) or
                not math.isfinite(h) or not math.isfinite(l) or l <= 0 or
                l > min(o,c) or h < max(o,c)):
                raise ValueError("INVALID_RANGE")
            h,l=float(h),float(l)
        if timestamp in by_symbol.setdefault(symbol, {}):
            raise ValueError("DUPLICATE_BAR")
        by_symbol[symbol][timestamp] = (o,h,l,c,v)
    symbols = sorted(by_symbol)
    if len(symbols) != 2:
        raise ValueError("TWO_ASSETS_REQUIRED")
    times = sorted(by_symbol[symbols[0]])
    if len(times) < 240 or any(y-x != BAR for x, y in zip(times, times[1:])):
        raise ValueError("INSUFFICIENT_OR_GAPPED_HISTORY")
    if set(times) != set(by_symbol[symbols[1]]):
        raise ValueError("UNALIGNED_ASSETS")
    arrays = [[by_symbol[s][t] for t in times] for s in symbols]
    return {"symbols": symbols, "times": times, "bars": arrays, "source_sha256": sha(rows)}


def close(market, asset, index):
    return market["bars"][asset][index][3]


def targets(market, i):
    # Ranking uses only historical CLOSED bars, never the entry/open at i+2.
    scores = []
    for a in range(2):
        v = math.log(close(market, a, i)) - math.log(close(market, a, i-16))
        scores.append((v, -a, a))
    ordered = sorted(scores, reverse=True)
    return (None, ordered[0][2], ordered[1][2])


def observation(market, i, position):
    if i < LOOKBACK or i >= len(market["times"]):
        raise ValueError("FEATURE_WINDOW")
    vector = []
    for a in range(2):
        current = math.log(close(market, a, i))
        logs = [math.log(close(market, a, j)) for j in range(i-16, i+1)]
        step = [logs[k]-logs[k-1] for k in range(1,len(logs))]
        volatility = math.sqrt(math.fsum(x*x for x in step)/len(step))
        vector.extend(((current-logs[-5])*10, (current-logs[0])*4, volatility*20))
    vector.extend((float(position is None),float(position == 0),float(position == 1)))
    return [max(-5.,min(5.,float(x))) for x in vector]

def net_liquidation(cash, quantity, position, closes, cost=COST):
    value = cash if position is None else cash + quantity*closes[position]*(1-cost)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("INVALID_NAV")
    return value


def step(market, i, position, cash, quantity, action, *, cost=COST):
    """Causal executed target at i+2 open, scored only after i+H close.

    Rejected/insufficient-liquidity fill leaves original inventory unchanged;
    never invent a completed sale. Returns an auditable transition receipt.
    """
    if type(action) is not int or not 0 <= action < len(ACTIONS):
        raise ValueError("INVALID_ACTION")
    if i < LOOKBACK or i+HORIZON >= len(market["times"]):
        raise ValueError("UNMATURED_STEP")
    if not 0 <= cost < .5 or not all(math.isfinite(x) for x in (cash,quantity,cost)) or min(cash,quantity) < 0:
        raise ValueError("INVALID_ACCOUNT")
    before = net_liquidation(cash,quantity,position,[close(market,a,i) for a in range(2)],cost)
    prior_position = position
    ranks = targets(market,i)
    target = ranks[action]
    fill_at = i+DELAY
    fills = []
    rejected = None
    if target != position:
        # Preflight *both* legs atomically: do not sell an incumbent unless its
        # replacement can also be bought with modeled candle participation.
        sell = None
        if position is not None:
            sell = market["bars"][position][fill_at]
            if quantity > sell[4]*PARTICIPATION:
                rejected = "SELL_CAPACITY"
        proceeds = cash + (quantity*sell[0]*(1-cost) if sell else 0)
        candidate_qty = 0.
        if target is not None:
            buy = market["bars"][target][fill_at]
            candidate_qty = proceeds*(1-cost)/buy[0]
            if not math.isfinite(candidate_qty) or candidate_qty <= 0 or candidate_qty > buy[4]*PARTICIPATION:
                rejected = rejected or "BUY_CAPACITY"
        if not math.isfinite(proceeds) or proceeds <= 0:
            rejected = rejected or "NONFINITE_SETTLEMENT"
        if rejected is None:
            if position is not None:
                fills.append({"side":"sell","asset":position,"price":sell[0],"quantity":quantity})
            if target is not None:
                fills.append({"side":"buy","asset":target,"price":market["bars"][target][fill_at][0],"quantity":candidate_qty})
                cash, quantity, position = 0., candidate_qty, target
            else:
                cash, quantity, position = proceeds, 0., None
    after = net_liquidation(cash,quantity,position,[close(market,a,i+HORIZON) for a in range(2)],cost)
    reward = 100*math.log(after/before)
    if not math.isfinite(reward):
        raise ValueError("NONFINITE_REWARD")
    behavior = ("UNFILLED" if rejected else
                "ABSTAIN" if target == prior_position and prior_position is None else
                "HOLD" if target == prior_position else
                "DEFEND" if target is None else
                "REENTER" if prior_position is None else "ROTATE")
    event = {"signal_ts":market["times"][i]+BAR,
             "feature_sha256":sha(observation(market,i,prior_position)),
             "ranked_target_assets":list(ranks),
             "entry_ts":market["times"][fill_at],
             "matured_ts":market["times"][i+HORIZON]+BAR,
             "action":ACTIONS[action],"behavior":behavior,
             "requested_target":target,"position":position,
             "fills":fills,"unfilled_reason":rejected,
             "nav_before":before,"nav_after":after,"reward":reward}
    if not event["signal_ts"] < event["entry_ts"] < event["matured_ts"]:
        raise AssertionError("CAUSAL_TIMING")
    return position,cash,quantity,event


def rollout(market, begin, end, chooser, *, learn=None):
    position,cash,quantity = None,1000.,0.
    events=[];nav=[cash]
    for i in range(begin,end-HORIZON,HORIZON):
        before = observation(market,i,position)
        a=chooser(before,position,i)
        position,cash,quantity,event=step(market,i,position,cash,quantity,a)
        if learn is not None and event["unfilled_reason"] is None:
            # The agent receives its reward only AFTER the causal outcome matures.
            # Terminal training windows do not bootstrap into an unobserved episode.
            done = i + 2*HORIZON >= end
            learn(before,a,event["reward"],observation(market,i+HORIZON,position),done)
        events.append(event);nav.append(event["nav_after"])
    peak = nav[0]
    drawdown = 0.
    for v in nav:
        peak=max(peak,v)
        drawdown=max(drawdown,1-v/peak)
    return {"net_return":nav[-1]/nav[0]-1,"max_drawdown":drawdown,
            "periods":len(events),"unfilled":sum(bool(e["unfilled_reason"]) for e in events),
            "events":events,"event_sha256":sha(events),"final_nav":nav[-1],
            "period_log_returns":[math.log(e["nav_after"]/e["nav_before"]) for e in events]}


def fixed(market,name):
    def choose(_vector,position,i):
        ranks=targets(market,i)
        if name=="cash":return 0
        if name=="buy_hold_first":return ranks.index(0)
        if name=="leader":return 1
        if name=="trend_exit":return 1 if math.log(close(market,ranks[1],i)/close(market,ranks[1],i-16))>0 else 0
        raise ValueError("UNKNOWN_CONTROL")
    return choose


def core_learner(core_dir,seed):
    """No fallback: import ONLY an exact pinned, separate BEAN Git checkout."""
    root=Path(core_dir).expanduser().resolve()
    if not (root/".git").exists():
        raise ValueError("SEPARATE_BEAN_GIT_CHECKOUT_REQUIRED")
    actual=subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
    if actual!=CORE_REVISION:
        raise ValueError("UNPINNED_BEAN_CORE")
    file=root/"bean/optimization/sponge_dqn.py"
    blob=subprocess.check_output(["git","-C",str(root),"hash-object",str(file)],text=True).strip()
    if blob!=CORE_BLOB:
        raise ValueError("MODIFIED_BEAN_LEARNER")
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    import torch
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    from bean.optimization import sponge_dqn
    if not Path(sponge_dqn.__file__).resolve().is_relative_to(root):
        raise ValueError("BEAN_IMPORT_FROM_WRONG_CHECKOUT")
    return sponge_dqn.SpongeDQN(seed=seed,observation_size=9,actions=ACTIONS,
