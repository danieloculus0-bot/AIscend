"""Coinbase public-candle forward paper ledger for frozen AIscend strategies.

Forward means a policy was frozen and its signal persisted before entry/exit
candles were available. The fills are hypothetical and fee-adjusted, NOT venue
fills and NEVER authorize promotion or trading.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from .bean_five_minute import BAR, fetch_5m
from .core import app_data_dir
from .primary_strategy import proposals

POLICIES = {"trend", "breakout", "reversal",
            "trend_4h_hold", "trend_1d_hold"}
HORIZONS = {"trend": 72, "breakout": 12, "reversal": 3,
            "trend_4h_hold": 48, "trend_1d_hold": 288}


def policy_identity(product, policy):
    if (not isinstance(product, str) or not product.endswith(("-USD", "-USDC"))
            or policy not in POLICIES):
        raise ValueError("Expected frozen USD/USDC spot product and known policy")
    return hashlib.sha256(
        ("aiscend.paper.policy.v1:" + product + ":" + policy).encode()).hexdigest()


def _signals(product, policy, bars, signal_ts):
    times = sorted(t for t in bars if t <= signal_ts)
    if len(times) < 145 or times[-1] != signal_ts:
        return False
    tail = times[-145:]
    if any(tail[i+1] - tail[i] != BAR for i in range(144)):
        return False
    try:
        vals = [float(bars[t]["close"]) for t in tail]
        vol = [float(bars[t]["volume"]) for t in tail[-13:]]
        if not all(math.isfinite(x) and x > 0 for x in vals):
            return False
        ratio = vol[-1] / (sum(vol[:-1])/12) if (
            len(vol)==13 and all(math.isfinite(x) and x>=0 for x in vol)
            and sum(vol[:-1])>0) else 0.0
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return False
    if policy == "trend_4h_hold":
        return (sum(vals[-12:])/12 > sum(vals[-48:])/48
                and vals[-1] > sum(vals[-12:])/12)
    if policy == "trend_1d_hold":
        return (sum(vals[-48:])/48 > sum(vals[-144:])/144
                and vals[-1] > sum(vals[-48:])/48)
    row = {"product":product, "return_5m":vals[-1]/vals[-2]-1,
           "return_1h":vals[-1]/vals[-13]-1,
           "return_6h":vals[-1]/vals[-73]-1,
           "volume_ratio":ratio, "spread_bps":10}
    return bool(proposals(row, enabled=(policy,)))


def _db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS frozen_policy (
        product TEXT PRIMARY KEY, policy TEXT NOT NULL,
        policy_sha TEXT NOT NULL, frozen_at REAL NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_decisions (
        product TEXT NOT NULL, policy_sha TEXT NOT NULL,
        signal_ts INTEGER NOT NULL, observed_at REAL NOT NULL,
        exit_ts INTEGER NOT NULL, state TEXT NOT NULL,
        entry_price REAL, exit_price REAL, net_return REAL,
        reason TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(product, policy_sha, signal_ts))""")


def freeze(conn, *, product, policy, now):
    identity = policy_identity(product, policy)
    existing = conn.execute(
        "SELECT policy_sha FROM frozen_policy WHERE product=?", (product,)
    ).fetchone()
    if existing is not None and existing[0] != identity:
        raise ValueError("Frozen strategy changed; create a new clean research ledger")
    if existing is None:
        conn.execute(
            "INSERT INTO frozen_policy VALUES(?,?,?,?)",
            (product,policy,identity,float(now)))
    return identity


def observe_and_score(*, manifest, db_path=None, fetcher=fetch_5m,
                      clock=time.time, fee_bps=60.0, spread_bps=10.0):
    """Run ONCE using only completed candles; no exchange authentication."""
    if not 0 <= fee_bps <= 1000 or not 0 <= spread_bps <= 1000:
        raise ValueError("Invalid modeled trading costs")
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 20:
        raise ValueError("Supply 1-20 explicitly frozen training candidates")
    now = float(clock())
    last_completed = int(now//BAR)*BAR - BAR
    db_path = Path(db_path or app_data_dir()/"forward-paper.sqlite3")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cost = (fee_bps + spread_bps/2)/10000
    out = {"source":"live_observed_public_coinbase_candles",
           "paper_only":True, "actual_coinbase_fills":False,
           "checked_at":now, "products":{}, "promotion_authorized":False}
    with closing(sqlite3.connect(db_path,timeout=30)) as con:
        _db(con)
        for item in manifest:
            product = str(item.get("product") or "").upper()
            policy = str(item.get("policy") or "")
            if not item.get("training_only_frozen"):
                raise ValueError("Candidate must be frozen from training, not holdout")
            identity = freeze(con,product=product,policy=policy,now=now)
            con.commit()
            pending = con.execute(
                "SELECT signal_ts,exit_ts,observed_at FROM paper_decisions "
                "WHERE product=? AND policy_sha=? AND state='PENDING' "
                "ORDER BY signal_ts LIMIT 1", (product,identity)).fetchone()
            output = {"policy":policy, "status":"NO_SIGNAL"}
            if pending is not None:
                signal_ts,exit_ts,observed_at = pending
                if last_completed >= exit_ts:
                    # Need entry/exit OPEN candles. Exit bar must be completed
                    # when scoring to ensure no future access at observation.
                    history = fetcher(product,signal_ts,exit_ts+BAR)
                    required = range(signal_ts,exit_ts+BAR,BAR)
                    try:
                        if any(t not in history for t in required):
                            raise ValueError("Missing candles during observed position")
                        buy=float(history[signal_ts+BAR]["open"])
                        sell=float(history[exit_ts]["open"])
                        if not all(math.isfinite(x) and x>0 for x in (buy,sell)):
                            raise ValueError("Invalid Coinbase paper fill price")
                        net=(sell/buy)*(1-cost)**2-1
                        con.execute(
                            "UPDATE paper_decisions SET state='SCORED',entry_price=?,"
                            "exit_price=?,net_return=?,reason=? WHERE product=?"
                            " AND policy_sha=? AND signal_ts=? AND state='PENDING'",
                            (buy,sell,net,"hypothetical_next_candle_open_fee_net",
                             product,identity,signal_ts))
                        output={"policy":policy,"status":"SCORED",
                                "net_return":round(net,8)}
                    except (KeyError,ValueError,TypeError,OverflowError) as err:
                        con.execute(
                            "UPDATE paper_decisions SET state='DATA_GAP',reason=? "
                            "WHERE product=? AND policy_sha=? AND signal_ts=?",
                            (type(err).__name__,product,identity,signal_ts))
                        output={"policy":policy,"status":"DATA_GAP"}
                    con.commit()
                else:
                    output={"policy":policy,"status":"PENDING",
                            "exit_ts":exit_ts}
            else:
                # Only a signal detected while its last input bar was just
                # completed can open a paper position. Never backfill signals.
                complete_at=last_completed+BAR
                if not complete_at <= now < complete_at+BAR:
                    raise RuntimeError("Observation clock is not within live five-minute slot")
                bars=fetcher(product,last_completed-145*BAR,last_completed+BAR)
                if _signals(product,policy,bars,last_completed):
                    frozen_at=con.execute(
                        "SELECT frozen_at FROM frozen_policy WHERE product=?",
                        (product,)).fetchone()[0]
                    # Freeze was observed before the entry candle exists.
                    if frozen_at > complete_at + BAR:
                        raise RuntimeError("Frozen policy timestamp is in the future")
                    exit_ts=last_completed+(1+HORIZONS[policy])*BAR
                    con.execute(
                        "INSERT OR IGNORE INTO paper_decisions "
                        "(product,policy_sha,signal_ts,observed_at,exit_ts,state) "
                        "VALUES(?,?,?,?,?,'PENDING')",
                        (product,identity,last_completed,now,exit_ts))
                    con.commit()
                    output={"policy":policy,"status":"PENDING",
                            "signal_ts":last_completed,"exit_ts":exit_ts}
            out["products"][product]=output

        summary = {}
        for product,policy in [(str(x["product"]).upper(),x["policy"])
                                for x in manifest]:
            identity=policy_identity(product,policy)
            rows=con.execute(
                "SELECT state,net_return FROM paper_decisions "
                "WHERE product=? AND policy_sha=? ORDER BY signal_ts",
                (product,identity)).fetchall()
            equity=peak=1.0
            dd=0.0
            wins=trades=0
            states={}
            for state,ret in rows:
                states[state]=states.get(state,0)+1
                if state!="SCORED":
                    continue
                trades+=1
                wins+=int(ret>0)
                equity*=1+ret
                peak=max(peak,equity)
                dd=max(dd,1-equity/peak)
            summary[product]={"policy":policy,
                "scored_trades":trades,"wins":wins,
                "net_return":round(equity-1,8),
                "max_realized_drawdown":round(dd,8),
                "states":states,
                "candidate_validated":False}
        out["scorecard"]=summary
    return out
