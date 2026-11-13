"""Read-only bridge from BEAN research selections to live trade confirmation.

The school *never* places orders. This module retrieves its selected rule,
recomputes a causal target from new, fully closed Coinbase hourly candles, and
returns a reviewable buy-only confirmation. It cannot trigger a BUY or SELL.
"""
from __future__ import annotations

import json
import math
import threading
import time

from .bean_strategy_lab import BeanStrategyLab, HOUR, _choose, fetch_candles

MIN_BARS = 61
HISTORY_HOURS = 75
CACHE_SECONDS = 900


class BeanPolicyGate:
    def __init__(self, lab=None, *, fetcher=None, clock=None):
        self.lab = lab or BeanStrategyLab()
        self.fetcher = fetcher or fetch_candles
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._cached_key = None
        self._cached_at = 0.0
        self._cached_value = None

    def _selected(self):
        with self.lab._lock, self.lab._connect() as db:
            rows = db.execute(
                "SELECT ordinal, result FROM experiments "
                "WHERE state='AUTO_SELECTED' AND result IS NOT NULL "
                "ORDER BY ordinal DESC LIMIT 20"
            ).fetchall()
        for row in rows:
            try:
                report = json.loads(row["result"])
                candidate = self.lab._valid_lesson(report.get("candidate"))
                pairs = report.get("pairs")
                if (candidate and isinstance(pairs, list) and 1 <= len(pairs) <= 20
                        and all(isinstance(x, str) and x.endswith(("-USD", "-USDC"))
                                for x in pairs)
                        and len(set(pairs)) == len(pairs)):
                    return row["ordinal"], candidate, pairs
            except (ValueError, KeyError, TypeError):
                continue
        return None

    def assess(self):
        """Return factual policy evidence, or N/A. Never infer unavailable bars."""
        selected = self._selected()
        if selected is None:
            return {"status": "NO_SELECTED_POLICY", "trade_effect": "NONE",
                    "reason": "No valid saved AUTO_SELECTED BEAN strategy"}
        ordinal, candidate, pairs = selected
        now = float(self.clock())
        end = int(now // HOUR * HOUR)
        key = (ordinal, candidate, tuple(pairs), end)
        with self._lock:
            if (self._cached_key == key and self._cached_value is not None
                    and now - self._cached_at < CACHE_SECONDS):
                return dict(self._cached_value)

            result = self._compute(ordinal, candidate, pairs, end)
            self._cached_key, self._cached_at, self._cached_value = key, now, result
            return dict(result)

    def _compute(self, ordinal, candidate, pairs, end):
        histories = {}
        start = end - HISTORY_HOURS * HOUR
        for product in pairs:
            try:
                raw = self.fetcher(product, start, end)
                histories[product] = raw
            except Exception as exc:
                return self._unavailable(ordinal, f"{product} candles unavailable: {type(exc).__name__}")

        # Only exact matching hourly close samples, with a complete causal window.
        shared = set.intersection(*(set(x) for x in histories.values()))
        times = sorted(ts for ts in shared if start <= ts < end and ts % HOUR == 0)
        last = end - HOUR
        if not times or times[-1] != last:
            return self._unavailable(ordinal, "Latest completed hourly candle missing")
        streak = [last]
        while streak[-1] - HOUR in shared and len(streak) < HISTORY_HOURS:
            streak.append(streak[-1] - HOUR)
        times = sorted(streak)
        if len(times) < MIN_BARS:
            return self._unavailable(ordinal, "Fewer than 61 shared consecutive hourly candles")
        bars = []
        for ts in times:
            prices = {}
            for p in pairs:
                item = histories[p][ts]
                cl = float(item["close"])
                if cl <= 0 or not math.isfinite(cl):
                    return self._unavailable(ordinal, f"Invalid {p} close at {ts}")
                prices[p] = {"close": cl}
            bars.append({"ts": ts, "prices": prices})
        target = _choose(bars, len(bars), pairs, candidate)
        return {"status": "READY", "trade_effect": "BUY_CONFIRMATION_ONLY",
                "experiment": ordinal, "candidate": list(candidate),
                "target": target, "bar_end": end,
                "bars": len(bars), "markets": len(pairs),
                "reason": "Causal signal from complete Coinbase hourly closes"}

    @staticmethod
    def _unavailable(ordinal, reason):
        return {"status": "DATA_UNAVAILABLE", "trade_effect": "NONE",
                "experiment": ordinal, "target": None, "reason": reason}

    @staticmethod
    def permits_buy(policy, product):
        """No forcing trades: only veto an existing BUY if fresh data disagrees."""
        if policy.get("status") != "READY":
            return True
        target = policy.get("target")
        if target is None:
            return False
        # The Base smart wallet's WETH represents ETH; USD and USDC quote
        # are interchangeable for the *asset comparison*, not execution.
        base = str(product or "").upper().split("-")[0]
        target_base = str(target).upper().split("-")[0]
        if base == "WETH":
            base = "ETH"
        return base == target_base
