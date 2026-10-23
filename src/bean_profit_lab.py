"""Research-only BEAN fee-net expert search with causal, hash-chained evidence.

BEAN updates contextual Q rewards only after each completed four-hour candle.
An expert is selected using chronological training only; validation can veto it
but cannot replace it. Historical holdout never authorizes live promotion.
No wallets, exchange orders or private credentials are imported here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

BAR = 300
AGGREGATE_BARS = 48
STRATEGIES = ("bean_q", "trend_3d", "trend_7d", "breakout_7d", "volatility_guard")
COST_MODEL = "coinbase_next_4h_open_taker60_spread10_v1"
GENESIS = "0" * 64


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def chain(events):
    """Recompute the ledger, rejecting edits, deletions and reordered records."""
    tip = GENESIS
    for number, event in enumerate(events):
        if event.get("seq") != number or event.get("prev") != tip:
            raise ValueError("Corrupted BEAN ledger sequence")
        payload = {k: v for k, v in event.items() if k != "hash"}
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        if digest != event.get("hash"):
            raise ValueError("Corrupted BEAN ledger entry")
        tip = digest
    return tip


class Ledger:
    def __init__(self):
        self.entries = []
        self.tip = GENESIS

    def append(self, kind, **fields):
        event = {"seq": len(self.entries), "prev": self.tip, "type": kind, **fields}
        event["hash"] = hashlib.sha256(canonical(event).encode()).hexdigest()
        self.entries.append(event)
        self.tip = event["hash"]
        return event

    def save(self, path):
        path = Path(path)
        if path.exists():
            raise FileExistsError("Refuse to overwrite a prior BEAN run")
        if chain(self.entries) != self.tip:
            raise ValueError("Internal BEAN ledger failed verification")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(canonical(row) + "\n" for row in self.entries), encoding="utf-8")


def to_4h(bars):
    """Build four-hour bars only from 48 complete five-minute observations."""
    groups = {}
    for ts, row in bars.items():
        ts = int(ts)
        if ts % BAR:
            continue
        group = (ts // (BAR * AGGREGATE_BARS)) * (BAR * AGGREGATE_BARS)
        groups.setdefault(group, []).append((ts, row))
    result = []
    for ts, values in sorted(groups.items()):
        values.sort()
        if (len(values) != AGGREGATE_BARS or
                [t for t, _ in values] != list(range(ts, ts + BAR * AGGREGATE_BARS, BAR))):
            continue
        prices = []
        good = True
        for _, bar in values:
            try:
                item = {k: float(bar[k]) for k in ("open", "close", "volume")}
                if (not all(math.isfinite(v) for v in item.values()) or
                        min(item["open"], item["close"]) <= 0 or
                        item["volume"] < 0):
                    good = False
                    break
                prices.append(item)
            except (KeyError, ValueError, TypeError, OverflowError):
                good = False
                break
        if good:
            result.append({"ts": ts, "open": prices[0]["open"],
                           "close": prices[-1]["close"],
                           "volume": sum(p["volume"] for p in prices)})
    return result


def signal(history, policy):
    """Only completed prior candles can inform an entry decision."""
    if len(history) < 43:
        return 0
    closes = [float(row["close"]) for row in history]
    last = closes[-1]
    fast = sum(closes[-6:]) / 6
    mid = sum(closes[-18:]) / 18
    slow = sum(closes[-42:]) / 42
    if policy == "trend_3d":
        return int(fast > mid and last > fast)
    if policy == "trend_7d":
        return int(mid > slow and last > mid)
    if policy == "breakout_7d":
        return int(last > max(closes[-43:-1]))
    if policy == "volatility_guard":
        changes = [math.log(closes[i] / closes[i-1])
                   for i in range(len(closes)-17, len(closes))]
        sd = math.sqrt(sum((x - sum(changes)/len(changes))**2
                           for x in changes) / len(changes))
        return int(mid > slow and last > mid and sd < .035)
    if policy == "cash":
        return 0
    if policy == "buy_hold":
        return 1
    raise ValueError("Unknown research policy")


def bean_context(history):
    """Trend and volatility regimes computed without a future candle."""
    closes = [float(row["close"]) for row in history]
    fast = sum(closes[-6:]) / 6
    slow = sum(closes[-42:]) / 42
    regime = "up" if fast > slow * 1.002 else "down" if fast < slow * .998 else "flat"
    recent = [math.log(closes[i] / closes[i-1])
              for i in range(len(closes)-12, len(closes))]
    variance = sum(x*x for x in recent) / len(recent)
    vol = "volatile" if math.sqrt(variance) > .018 else "calm"
    return regime + "/" + vol


def bean_action(q, context, prior_position):
    """Learned Q advantages must exceed a fixed anti-churn buffer."""
    state = q.get(context, {})
    long_score = float(state.get("long", 0.0))
    cash_score = float(state.get("cash", 0.0))
    switch_buffer = 14.0
    return int(long_score > cash_score +
               (switch_buffer if not prior_position else -switch_buffer))


def simulate(rows, policy, start, end, *, fee_bps=60.0, spread_bps=10.0,
             ledger=None, product=None, phase=None, initial_q=None):
    if fee_bps < 0 or spread_bps < 0 or fee_bps + spread_bps/2 >= 10000:
        raise ValueError("Invalid trading cost assumption")
    if not (43 <= start < end <= len(rows)):
        raise ValueError("Invalid chronological evaluation window")
    cost = (fee_bps + spread_bps / 2) / 10000
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    position = 0
    prev_close = None
    orders = 0
    wins = 0
    roundtrips = 0
    entry_equity = None
    gap_count = 0
    q = {k: dict(v) for k, v in (initial_q or {}).items()}
    for i in range(start, end):
        now = rows[i]
        previous = rows[i-1]
        if now["ts"] != previous["ts"] + BAR * AGGREGATE_BARS:
            gap_count += 1
            if position:
                return {"status": "DATA_GAP", "orders": orders, "gap_count": gap_count}
            prev_close = None
            continue
        context = bean_context(rows[:i]) if policy == "bean_q" else None
        target = bean_action(q, context, position) if context else signal(rows[:i], policy)
        if prev_close is not None and position:
            equity *= now["open"] / prev_close
        prior_position = position
        if position != target:
            equity *= 1 - cost
            orders += 1
            if position and entry_equity is not None:
                roundtrips += 1
                wins += int(equity > entry_equity)
                entry_equity = None
            elif target:
                entry_equity = equity
            position = target
        if position:
            equity *= now["close"] / now["open"]
        # Full-information RESEARCH rewards: both alternative actions are
        # scored after the candle completes, never ahead of execution.
        if context:
            bucket = q.setdefault(context, {"long": 0.0, "cash": 0.0})
            for action, gross in (("long", now["close"]/now["open"] - 1),
                                  ("cash", 0.0)):
                after = int(action == "long")
                fee = cost if after != prior_position else 0.0
                reward_bps = ((1-fee) * (1+gross) - 1) * 10000
                bucket[action] += .08 * (reward_bps - bucket[action])
            if ledger is not None:
                ledger.append("bean_q_reward_update", product=product, phase=phase,
                              observed_after_ts=now["ts"] + BAR * AGGREGATE_BARS,
                              context=context, chosen_target=target,
                              scored_long_bps=round(bucket["long"], 7),
                              scored_cash_bps=round(bucket["cash"], 7))
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity/peak)
        if ledger is not None:
            ledger.append("causal_decision", product=product, phase=phase,
                          policy=policy, decision_ts=previous["ts"] + BAR*AGGREGATE_BARS,
                          execution_ts=now["ts"], target=target,
                          equity=round(equity, 10), orders=orders)
        prev_close = now["close"]
    if position:
        equity *= 1 - cost
        orders += 1
        roundtrips += 1
        if entry_equity is not None:
            wins += int(equity > entry_equity)
    return {"status": "OK", "net_return": round(equity-1, 8),
            "drawdown": round(max_dd, 8), "orders": orders,
            "roundtrips": roundtrips, "wins": wins, "gap_count": gap_count,
            "fee_bps": fee_bps, "spread_bps": spread_bps,
            "learner_q": q if policy == "bean_q" else None}


def experiment(product, data, *, fee_bps=60.0, spread_bps=10.0):
    """Train -> validation veto -> locked historical holdout; never live-promote."""
    rows = to_4h(data)
    if len(rows) < 180:
        return {"status": "INSUFFICIENT_HISTORY", "product": product,
                "4h_bars": len(rows), "promotion_authorized": False}
    cut1 = int(len(rows) * .55)
    cut2 = int(len(rows) * .75)
    if cut1 < 50 or cut2-cut1 < 20 or len(rows)-cut2 < 20:
        return {"status": "INSUFFICIENT_HISTORY", "product": product,
                "4h_bars": len(rows), "promotion_authorized": False}
    ledger = Ledger()
    ledger.append("preregister", schema="bean-profit-search-v1", product=product,
                  fee_bps=fee_bps, spread_bps=spread_bps,
                  aggregation_minutes=240, experts=STRATEGIES,
                  training_cutoff=rows[cut1]["ts"],
                  validation_cutoff=rows[cut2]["ts"])
    training = {p: simulate(rows, p, 43, cut1, fee_bps=fee_bps,
                            spread_bps=spread_bps) for p in STRATEGIES}
    ranked = sorted(STRATEGIES, key=lambda p:
                    (training[p].get("net_return", -float("inf")), p), reverse=True)
    champion = ranked[0]
    ledger.append("bean_train_expert", product=product, champion=champion,
                  ranking=[{"policy": p, "train": training[p]} for p in ranked])
    traced_train = simulate(rows, champion, 43, cut1, fee_bps=fee_bps,
                            spread_bps=spread_bps, ledger=ledger,
                            product=product, phase="train")
    if traced_train["net_return"] != training[champion]["net_return"]:
        raise ValueError("BEAN ledger replay disagrees with training")
    validation = simulate(rows, champion, cut1, cut2, fee_bps=fee_bps,
                          spread_bps=spread_bps, ledger=ledger,
                          product=product, phase="validation",
                          initial_q=traced_train.get("learner_q"))
    val_cash = simulate(rows, "cash", cut1, cut2,
                        fee_bps=fee_bps, spread_bps=spread_bps)
    val_buy_hold = simulate(rows, "buy_hold", cut1, cut2,
                            fee_bps=fee_bps, spread_bps=spread_bps)
    frozen = bool(training[champion]["status"] == "OK" and
                  training[champion]["net_return"] > 0 and
                  training[champion]["roundtrips"] >= 2 and
                  validation["status"] == "OK" and
                  validation["net_return"] > 0 and
                  validation["net_return"] > val_cash["net_return"])
    ledger.append("freeze_before_holdout", product=product, policy=champion,
                  training_return=training[champion]["net_return"],
                  validation=validation, validation_cash=val_cash,
                  validation_buy_hold=val_buy_hold, eligible=frozen)
    test = simulate(rows, champion, cut2, len(rows),
                    fee_bps=fee_bps, spread_bps=spread_bps,
                    ledger=ledger, product=product, phase="holdout",
                    initial_q=validation.get("learner_q"))
    cash = simulate(rows, "cash", cut2, len(rows),
                    fee_bps=fee_bps, spread_bps=spread_bps)
    buy_hold = simulate(rows, "buy_hold", cut2, len(rows),
                        fee_bps=fee_bps, spread_bps=spread_bps)
    historical_pass = bool(frozen and test["status"] == "OK" and
                           test["net_return"] > 0 and
                           test["net_return"] > cash["net_return"] and
                           test["net_return"] > buy_hold["net_return"])
    ledger.append("historical_holdout_result", product=product, policy=champion,
                  holdout=test, holdout_cash=cash,
                  holdout_buy_hold=buy_hold, historical_pass=historical_pass,
                  promotion_authorized=False)
    return {"status": "HISTORICAL_OOS_PASS" if historical_pass else "NOT_PROVEN",
            "product": product, "4h_bars": len(rows),
            "champion": champion, "training": training,
            "validation": validation, "holdout": test,
            "holdout_cash": cash, "holdout_buy_hold": buy_hold,
            "eligible_before_holdout": frozen, "promotion_authorized": False,
            "ledger_tip": ledger.tip, "ledger_events": ledger.entries}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", nargs="+", default=[
        "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "AVAX-USD", "DOGE-USD"])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--fee-bps", type=float, default=60.)
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/bean-profit-search.json"))
    args = parser.parse_args(argv)
    if not 30 <= args.days <= 120:
        parser.error("Days must be 30-120")
    from .strategy_research import historical_bars
    reports = {}
    for market in args.products:
        try:
            candles = historical_bars(market, days=args.days)
            report = experiment(market, candles, fee_bps=args.fee_bps)
            reports[market] = {k: v for k, v in report.items() if k != "ledger_events"}
            if "ledger_events" in report:
                path = args.output.parent / (market.replace("-", "_") + "-bean-ledger.jsonl")
                ledger = Ledger()
                ledger.entries = report["ledger_events"]
                ledger.tip = report["ledger_tip"]
                ledger.save(path)
        except Exception as exc:
            reports[market] = {"status": "DATA_UNAVAILABLE",
                               "error_type": type(exc).__name__,
                               "promotion_authorized": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = {"schema": "bean-profit-search-v1", "cost_model": COST_MODEL,
              "created_at": time.time(), "days": args.days,
              "reports": reports, "promotion_authorized": False,
              "note": "Historical only; no live orders or verified forward profit"}
    args.output.write_text(json.dumps(
        output, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({
        k: {"status": v["status"], "champion": v.get("champion"),
            "holdout_net": (v.get("holdout") or {}).get("net_return")}
        for k, v in reports.items()}, sort_keys=True))
    return 0 if all(v["status"] != "DATA_UNAVAILABLE" for v in reports.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
