"""Causal prospective BEAN/Coinbase public-paper ledger for frozen candidates.

Research only. No Coinbase credentials, wallet imports or order submissions.
All hypothetical entry opens occur AFTER the signal was committed, and scores
appear only after the corresponding six-hour candle closes.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import hashlib
import json
import math
import sqlite3
import time

from .bean_profit_lab import GENESIS, bean_action, bean_context, canonical, chain, signal
from .bean_six_hour_universe import BAR, fetch_history


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _append(db, kind, *, product=None, state=None, **details):
    events = [json.loads(row[0]) for row in
              db.execute("SELECT data FROM events ORDER BY seq")]
    entry = {"seq": len(events), "prev": chain(events), "type": kind, **details}
    if product is not None:
        entry["product"] = product
    if state is not None:
        encoded = canonical(state)
        entry["state_sha"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        db.execute(
            "INSERT INTO positions(product,state) VALUES(?,?) "
            "ON CONFLICT(product) DO UPDATE SET state=excluded.state",
            (product, encoded))
    entry["hash"] = digest(entry)
    db.execute("INSERT INTO events(seq,data) VALUES(?,?)",
               (entry["seq"], canonical(entry)))
    return entry["hash"]


def _verify(db):
    events = [json.loads(row[0]) for row in
              db.execute("SELECT data FROM events ORDER BY seq")]
    tip = chain(events)
    last = {e["product"]: e["state_sha"]
            for e in events if "state_sha" in e}
    persisted = dict(db.execute("SELECT product,state FROM positions"))
    if set(last) != set(persisted):
        raise ValueError("BEAN state is missing from append-only evidence")
    for product, state in persisted.items():
        if hashlib.sha256(state.encode("utf-8")).hexdigest() != last[product]:
            raise ValueError("BEAN state was modified without ledger evidence")
    return tip


def _history(raw, now, *, strict_features=True):
    end = now // BAR * BAR
    rows = sorted((dict(r) for r in raw.values()
                   if int(r["ts"]) + BAR <= end),
                  key=lambda r: r["ts"])
    for r in rows:
        if (r["ts"] % BAR or
                not all(math.isfinite(float(r[k])) for k in ("open", "close")) or
                min(float(r["open"]), float(r["close"])) <= 0):
            raise ValueError("Invalid six-hour Coinbase candle")
    if len(rows) < 44 or rows[-1]["ts"] != end - BAR:
        raise ValueError("Current completed six-hour candle missing")
    if strict_features and any(rows[i]["ts"] != rows[i-1]["ts"] + BAR
           for i in range(len(rows)-43, len(rows))):
        raise ValueError("Missing required contiguous feature history")
    return rows


def _signal(db, candidate, state, rows, now):
    if state.get("pending") or state["blocked"]:
        return
    policy = candidate["policy"]
    if policy == "bean_q":
        context = bean_context(rows, BAR)
        target = bean_action(state["q"], context, state["held"])
    else:
        context = None
        target = signal(rows, policy, BAR)
    entry_ts = (now // BAR + 1) * BAR
    if entry_ts <= now or entry_ts <= state["frozen_at_unix"]:
        raise ValueError("Cannot fill at an already-open historical candle")
    state["pending"] = {
        "selected_at": now, "entry_ts": entry_ts,
        "context": context, "target": target,
        "observed_cutoff": rows[-1]["ts"] + BAR,
    }
    _append(db, "preentry_signal", product=candidate["product"],
            state=state, pending=state["pending"])


def _score(db, candidate, state, row):
    ts = int(row["ts"])
    if ts != state["last_seen_ts"] + BAR:
        state["blocked"] = "DATA_GAP"
        _append(db, "data_gap", product=candidate["product"], state=state,
                expected_ts=state["last_seen_ts"] + BAR, observed_ts=ts)
        return
    opening, close = float(row["open"]), float(row["close"])
    cost = state["cost"]
    was_held = state["held"]
    previous_close = state["last_close"]
    if was_held:
        state["equity"] *= opening / previous_close

    pending = state["pending"]
    if pending and ts >= pending["entry_ts"]:
        if ts != pending["entry_ts"] or pending["selected_at"] >= ts:
            state["blocked"] = "MISSED_COMMITTED_ENTRY"
            _append(db, "missed_entry", product=candidate["product"],
                    state=state, observed_ts=ts)
            return
        target = pending["target"]
        if target not in (0, 1):
            raise ValueError("Invalid committed target")
        if target != was_held:
            pre_fee_equity = state["equity"]
            state["equity"] *= 1 - cost
            state["orders"] += 1
            if was_held:
                state["roundtrips"] += 1
                state["wins"] += int(
                    state["equity"] > state.get("entry_equity", 1.))
                state["entry_equity"] = None
            else:
                state["entry_equity"] = pre_fee_equity
            state["held"] = target
        if state["benchmark_first_open"] is None:
            state["benchmark_first_open"] = opening
        state["pending"] = None

    if state["held"]:
        state["equity"] *= close / opening

    if pending and ts == pending["entry_ts"] and candidate["policy"] == "bean_q":
        context = pending["context"]
        scores = state["q"].setdefault(context, {"long": 0., "cash": 0.})
        held_gap = opening / previous_close if was_held else 1.
        for action, gross in (("long", close / opening - 1), ("cash", 0.)):
            target = int(action == "long")
            side_cost = cost if target != was_held else 0.
            reward_bps = (held_gap*(1-side_cost)*(1+gross)-1)*10000
            scores[action] += .08*(reward_bps - scores.get(action, 0.))
        state["q_updates"] += 1
        _append(db, "postclose_q_update", product=candidate["product"],
                state=state, candle_start=ts, observed_after_ts=ts+BAR,
                context=context, q=scores)

    state["last_close"] = close
    state["last_seen_ts"] = ts
    state["forward_bars"] += 1
    liquidation = state["equity"]*(1-cost if state["held"] else 1.)
    state["peak"] = max(state["peak"], liquidation)
    state["max_drawdown"] = max(
        state["max_drawdown"], 1-liquidation/state["peak"])
    state["equity_liquidatable"] = liquidation
    if state["benchmark_first_open"] is not None:
        state["buy_hold_net"] = (
            close/state["benchmark_first_open"]*(1-cost)**2 - 1)
    _append(db, "completed_candle_mark", product=candidate["product"],
            state=state, candle_start=ts, observed_after_ts=ts+BAR,
            liquidation_value=round(liquidation, 10))


def inspect(manifest_path, db_path, *, clock=None, history_fetcher=None):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("schema") != "aiscend.bean.sixhour.prospective.freeze.v1":
        raise ValueError("Unknown BEAN prospective freeze manifest")
    if manifest.get("promotion_authorized") is not False:
        raise ValueError("Cannot accept live-promotion authority from a manifest")
    now = int((clock or time.time)())
    if now <= manifest["frozen_at_unix"]:
        return {"status": "PRE_FREEZE", "promotion_authorized": False}
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_sha = digest(manifest)

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS positions("
                   "product TEXT PRIMARY KEY,state TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS events("
                   "seq INTEGER PRIMARY KEY,data TEXT NOT NULL)")
        row = db.execute("SELECT v FROM meta WHERE k='manifest_sha'").fetchone()
        if row and row[0] != manifest_sha:
            raise ValueError("Cannot change BEAN freeze, Q model or fee assumptions")
        if row is None:
            db.execute("INSERT INTO meta(k,v) VALUES('manifest_sha',?)",
                       (manifest_sha,))
            _append(db, "freeze_preregistered", manifest_sha=manifest_sha,
                    frozen_at=manifest["frozen_at_unix"])
        _verify(db)
        reports = {}
        for candidate in manifest["candidates"]:
            product = candidate["product"]
            stored = db.execute(
                "SELECT state FROM positions WHERE product=?", (product,)
            ).fetchone()
            state = json.loads(stored[0]) if stored else None
            if state and state["blocked"]:
                reports[product] = {"status": state["blocked"],
                                    "promotion_authorized": False}
                continue
            try:
                raw = (
                    history_fetcher(product, days=45, clock=lambda: now)
                    if history_fetcher else
                    fetch_history(product, days=45, clock=lambda: now))
                rows = _history(raw, now, strict_features=state is None)
            except Exception as exc:
                reports[product] = {
                    "status": "DATA_UNAVAILABLE",
                    "reason_type": type(exc).__name__,
                    "promotion_authorized": False}
                continue

            if state is None:
                state = {
                    "frozen_at_unix": manifest["frozen_at_unix"],
                    "last_seen_ts": rows[-1]["ts"],
                    "last_close": float(rows[-1]["close"]),
                    "initialized_at": now, "blocked": None,
                    "q": candidate.get("q_seed") or {},
                    "equity": 1., "equity_liquidatable": 1.,
                    "held": 0, "entry_equity": None, "pending": None,
                    "orders": 0, "roundtrips": 0, "wins": 0,
                    "q_updates": 0, "forward_bars": 0,
                    "peak": 1., "max_drawdown": 0.,
                    "cost": (manifest["fee_bps_per_side"] +
                             manifest["spread_bps_roundtrip"]/2)/10000,
                    "benchmark_first_open": None, "buy_hold_net": None,
                }
                _append(db, "first_prospective_observation",
                        product=product, state=state, observed_at=now,
                        last_completed_start=rows[-1]["ts"])
            else:
                unseen = [bar for bar in rows
                          if int(bar["ts"]) > state["last_seen_ts"]]
                for bar in unseen:
                    _score(db, candidate, state, bar)
                    if state["blocked"]:
                        break
            if not state["blocked"]:
                _signal(db, candidate, state, rows, now)
            net = state["equity_liquidatable"] - 1.
            passive = state["buy_hold_net"]
            qualifies = bool(
                state["forward_bars"] >= manifest["min_forward_completed_bars"]
                and state["roundtrips"] >= manifest["min_realized_roundtrips"]
                and net > manifest["min_excess_return_vs_cash"]
                and passive is not None
                and net > passive + manifest["min_excess_return_vs_buy_hold"]
                and state["max_drawdown"] <= manifest["max_forward_drawdown"])
            reports[product] = {
                "status": state["blocked"] or
                    ("FORWARD_PAPER_GATE_PASS" if qualifies else "COLLECTING"),
                "forward_bars": state["forward_bars"],
                "roundtrips": state["roundtrips"],
                "orders": state["orders"],
                "q_updates": state["q_updates"],
                "fee_net_paper_return": round(net, 8),
                "buy_hold_net": round(passive, 8) if passive is not None else None,
                "max_drawdown": round(state["max_drawdown"], 8),
                "pending": state["pending"],
                "promotion_authorized": False,
            }
        tip = _verify(db)
        return {
            "status": "OBSERVED", "as_of_unix": now,
            "manifest_sha": manifest_sha, "ledger_tip": tip,
            "markets": reports, "promotion_authorized": False,
            "note": "Prospective hypothetical Coinbase public candles only; no live fill or verified future profit",
        }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=Path(
        "artifacts/bean-sixhour-prospective.sqlite3"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = inspect(args.manifest, args.db)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
