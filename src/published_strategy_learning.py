"""Persistent, genuinely forward-only learning for published trading rules.

Frozen strategy identity and a post-freeze paper scoreboard survive desktop
restarts. Rule changes are recommendations for research only, never orders.
"""
from __future__ import annotations

import hashlib
import math
import json
from pathlib import Path
import sqlite3
import time

from .published_strategy_research import (
    MIN_BARS, RULES, WARMUP, normalize, simulate, _passive,
    DEFAULT_FEE_BPS, DEFAULT_SPREAD_BPS,
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


class PublishedRuleLearner:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS freezes (
                product TEXT PRIMARY KEY,
                freeze_ts INTEGER NOT NULL, frozen_rule TEXT NOT NULL,
                last_ts INTEGER NOT NULL, evidence TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                product TEXT NOT NULL, last_ts INTEGER NOT NULL,
                prev_sha TEXT NOT NULL, event_sha TEXT NOT NULL,
                evidence TEXT NOT NULL,
                PRIMARY KEY (product,last_ts))""")

    def frozen(self, product):
        with sqlite3.connect(self.path) as db:
            saved = db.execute(
                "SELECT frozen_rule FROM freezes WHERE product=?",
                (str(product).upper(),)).fetchone()
        return tuple(json.loads(saved[0])) if saved else None

    def observe(self, product, raw, *, frozen_rule=None, clock=None,
                fee_bps=DEFAULT_FEE_BPS, spread_bps=DEFAULT_SPREAD_BPS):
        """First call freezes selection; later calls score only new future bars."""
        try:
            rows = normalize(raw)
        except ValueError as exc:
            if str(exc) == "PARTIAL":
                return {"status": "PARTIAL", "promotion_authorized": False}
            raise
        if len(rows) < MIN_BARS:
            return {"status": "INSUFFICIENT_HISTORY", "promotion_authorized": False}
        # Frozen prospective research must start at the ACTUAL current
        # completed candle, never at an arbitrary historical backtest date.
        now = int((clock or time.time)())
        completed_at = rows[-1]["ts"] + 21600
        if not completed_at <= now < completed_at + 21600:
            return {"status": "DATA_UNAVAILABLE",
                    "reason": "NO_CURRENT_COMPLETED_CANDLE",
                    "promotion_authorized": False}
        modeled_cost = (float(fee_bps)+float(spread_bps)/2)/10000
        if (not math.isfinite(modeled_cost) or not 0 <= modeled_cost < 1
                or fee_bps < 0 or spread_bps < 0):
            raise ValueError("INVALID_COST")
        product = str(product).upper().strip()
        rule = tuple(frozen_rule or ("donchian_20_10",))
        if rule not in RULES:
            raise ValueError("UNKNOWN_RULE")
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute(
                "SELECT freeze_ts,frozen_rule,last_ts,evidence "
                "FROM freezes WHERE product=?", (product,)).fetchone()
            if saved is None:
                freeze_at = rows[-1]["ts"]
                record = {"status": "FROZEN", "frozen_rule": list(rule),
                          "freeze_ts": freeze_at, "fee_bps": fee_bps,
                          "spread_bps": spread_bps, "observed_forward_bars": 0,
                          "learned_rule": None, "promotion_authorized": False}
                db.execute(
                    "INSERT INTO freezes(product,freeze_ts,frozen_rule,last_ts,evidence) "
                    "VALUES(?,?,?,?,?)",
                    (product, freeze_at, _canonical(rule), freeze_at,
                     _canonical(record)))
                return record
            freeze_ts, frozen, last_ts, stored = saved
            frozen = tuple(json.loads(frozen))
            original = json.loads(stored)
            if (float(original["fee_bps"]) != float(fee_bps)
                    or float(original["spread_bps"]) != float(spread_bps)):
                raise ValueError("FROZEN_COST_MODEL_MISMATCH")
            # No retroactive switching after freeze, even if caller changes it.
            if frozen_rule is not None and rule != frozen:
                raise ValueError("FROZEN_RULE_MISMATCH")
            if rows[-1]["ts"] < last_ts:
                return {"status": "STALE", "promotion_authorized": False}
            if rows[-1]["ts"] == last_ts:
                return json.loads(stored)
            index = next((i for i, bar in enumerate(rows)
                          if bar["ts"] == freeze_ts), None)
            if index is None or index < WARMUP:
                return {"status": "PARTIAL", "reason": "FREEZE_HISTORY_GAP",
                        "promotion_authorized": False}
            # Every prospective bar must still exist in the current download.
            forward_start = index + 1
            costs = (fee_bps+spread_bps/2)/10000
            outcomes = []
            for config in RULES:
                result = simulate(rows, config, start=forward_start,
                                  end=len(rows), fee_bps=fee_bps,
                                  spread_bps=spread_bps)
                outcomes.append({"rule": list(config), **result})
            passive = _passive(rows, forward_start, len(rows), costs)
            # Paper-learning needs a meaningful prospective sample, not two
            # lucky five-minute decisions after deployment.
            enough_forward = len(rows)-index-1 >= 120
            valid = [r for r in outcomes if enough_forward
                     and r["roundtrips"] >= 3
                     and r["net_return"] > max(0., passive)]
            learned = (max(valid, key=lambda r: (
                r["net_return"]-.4*r["max_drawdown"], r["net_return"]))["rule"]
                       if valid else None)
            report = {"status": "FORWARD_PAPER_OBSERVED",
                      "frozen_rule": list(frozen),
                      "freeze_ts": freeze_ts, "fee_bps": fee_bps,
                      "spread_bps": spread_bps,
                      "observed_forward_bars": len(rows)-index-1,
                      "first_forward_bar": rows[forward_start]["ts"],
                      "last_forward_bar": rows[-1]["ts"],
                      "outcomes": outcomes,
                      "buy_hold_net": round(passive, 8),
                      "learned_rule": learned,
                      "adoption": "RESEARCH_ONLY_NOT_DEPLOYED",
                      "promotion_authorized": False}
            predecessor = db.execute(
                "SELECT event_sha FROM observations WHERE product=? "
                "ORDER BY last_ts DESC LIMIT 1", (product,)).fetchone()
            prev_sha = predecessor[0] if predecessor else "0"*64
            evidence = _canonical(report)
            event_sha = hashlib.sha256(
                (prev_sha+evidence).encode("utf-8")).hexdigest()
            db.execute(
                "INSERT INTO observations(product,last_ts,prev_sha,event_sha,evidence) "
                "VALUES(?,?,?,?,?)",
                (product, rows[-1]["ts"], prev_sha, event_sha, evidence))
            db.execute(
                "UPDATE freezes SET last_ts=?,evidence=? WHERE product=?",
                (rows[-1]["ts"], evidence, product))
            return report

    def audit(self, product):
        """Check that all saved forward snapshots remain linked and unmodified."""
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT prev_sha,event_sha,evidence FROM observations "
                "WHERE product=? ORDER BY last_ts", (product.upper(),)).fetchall()
            prev = "0"*64
            for parent, event, payload in rows:
                if parent != prev or hashlib.sha256(
                        (parent+payload).encode("utf-8")).hexdigest() != event:
                    raise ValueError("CORRUPT_FORWARD_RESEARCH")
                prev = event
        return {"status": "LEDGER_VERIFIED", "events": len(rows),
                "tip": prev, "promotion_authorized": False}
