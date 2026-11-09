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
