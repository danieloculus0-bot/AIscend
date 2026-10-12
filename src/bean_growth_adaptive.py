"""Causal five-minute BEAN reward-learning gate for AIscend.

Research uses completed public Coinbase candles and executes hypothetical
positions for reward feedback. The adopted action actively chooses long/cash
on the next live engine cycle. No wallet keys or exchange execution in module.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import math
import random
import sqlite3
import threading
import time
from pathlib import Path

from .bean_five_minute import BAR, fetch_5m
from .core import app_data_dir

ACTIONS = ("momentum", "reversion", "trend_3", "buy_hold", "cash")
MODEL_FEE = .0007
LEARNING_RATE = .08
PAPER_EXPLORATION = .12
MIN_HISTORY = 12
MIN_FORWARD_BARS = 6


def _features(returns):
    if len(returns) < MIN_HISTORY:
        return "initial"
    tail = returns[-11:]
    same = sum(a * b > 0 for a, b in zip(tail[:-1], tail[1:]))
    regime = "persistent" if same >= 7 else "alternating" if same <= 3 else "mixed"
    return regime + ("/up" if returns[-1] > 0 else "/down")


def _position(action, returns):
    if action == "cash":
        return 0
    if action == "buy_hold":
        return 1
    if not returns:
        return 0
    if action == "momentum":
        return int(returns[-1] > 0)
    if action == "reversion":
        return int(returns[-1] < 0)
    if action == "trend_3":
        return int(sum(returns[-3:]) > 0)
    raise ValueError("unknown action")


def _choose(state, context, *, explore, seed):
    q = state["q"].get(context, {})
    choice = max(ACTIONS, key=lambda a: (q.get(a, 0.0), -ACTIONS.index(a)))
    if explore:
        rng = random.Random(seed)
        if rng.random() < PAPER_EXPLORATION:
            return rng.choice(ACTIONS)
    return choice


def _fresh_state():
    return {"schema": "aiscend.bean.growth.v1", "last_bar": None,
            "last_close": None, "recent_returns": [], "pending": None,
            "held": 0, "equity": 100.0, "peak": 100.0, "drawdown": 0.0,
            "q": {}, "visits": {}, "paper_bars": 0, "forward_bars": 0,
            "mock_trades": 0, "history_gaps": 0, "bootstrapped": False,
            "strategy_changes": 0, "last_action": None}


class BeanAdaptiveGrowthGate:
    """Executes hypothetical positions, updates rewards, adopts scored policy.

    Evaluation fetches completed five-minute public candles. First assessment
    trains chronologically, subsequent assessments continue online learning.
    Adopted exposure directly informs BUY, SELL or HOLD on the next cycle.
    Money is handled only by the existing AIscend execution engines.
    """
    def __init__(self, *, path=None, fetcher=None, clock=None):
        self.path = Path(path) if path else app_data_dir() / "bean-growth-policy.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or fetch_5m
        self.clock = clock or time.time
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS policy (product TEXT PRIMARY KEY, state TEXT NOT NULL)")


    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def mode():
        return "live"

    @staticmethod
    def _product(product):
        name = str(product or "").upper().strip()
        if name in ("ETH", "WETH", "WETH-USDC"):
            return "ETH-USD"
        if len(name) > 36 or not name or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in name):
            raise ValueError("invalid market product")
        if "-" not in name:
            return name + "-USD"
        if not name.endswith(("-USD", "-USDC")):
            raise ValueError("unsupported quote currency")
        return name

    @staticmethod
    def _apply_bar(state, bar, ts, product):
        """One actual completed price bar, no future data in chosen action."""
        opening, close = float(bar["open"]), float(bar["close"])
        if not all(math.isfinite(v) and v > 0 for v in (opening, close)):
            raise ValueError("invalid price")
        prior = state["last_bar"]
        if prior is not None and ts <= prior:
            return False
        if prior is not None and ts != prior + BAR:
            state["history_gaps"] += 1
            state["pending"] = None
            state["last_close"] = None
            state["recent_returns"] = []
        pending = state["pending"]
        if pending is not None:
            held = int(state["held"])
            target = int(pending["position"])
            transaction_cost = MODEL_FEE if held != target else 0.0
            realized = target * (close / opening - 1.0) - transaction_cost
            state["equity"] *= (1 + realized)
            if state["equity"] <= 0:
                raise ValueError("simulated paper capital exhausted")
            if transaction_cost:
                state["mock_trades"] += 1
            state["held"] = target
            state["peak"] = max(state["peak"], state["equity"])
            state["drawdown"] = max(state["drawdown"], 1-state["equity"]/state["peak"])
            context, action = pending["context"], pending["action"]
            q = state["q"].setdefault(context, {})
            old = float(q.get(action, 0.0))
            q[action] = old + LEARNING_RATE * (realized * 10000.0 - old)
            counts = state["visits"].setdefault(context, {})
            counts[action] = counts.get(action, 0) + 1
            state["paper_bars"] += 1
            if state["bootstrapped"]:
                state["forward_bars"] += 1
        prior_close = state["last_close"]
        returns = state["recent_returns"]
        if prior_close is not None:
            returns.append(close / prior_close - 1.0)
            del returns[:-64]
        state["last_close"], state["last_bar"] = close, ts
        context = _features(returns)
        salt = int(hashlib.sha256(f"{product}:{ts}".encode()).hexdigest()[:12], 16)
        action = _choose(state, context, explore=True, seed=salt)
        if state["last_action"] is not None and state["last_action"] != action:
            state["strategy_changes"] += 1
        state["last_action"] = action
        state["pending"] = {"context": context, "action": action,
                            "position": _position(action, returns)}
        return True

    def assess(self, product):
        market = self._product(product)
        end = int(self.clock() // BAR) * BAR
        start = end - 280 * BAR
        try:
            data = self.fetcher(market, start, end)
            valid_times = sorted(ts for ts in data
                                 if start <= ts < end and ts % BAR == 0)
            if len(valid_times) < 30 or valid_times[-1] != end - BAR:
                raise ValueError("latest 5-minute market history incomplete")
            with self._lock, self._db() as db:
                row = db.execute("SELECT state FROM policy WHERE product=?", (market,)).fetchone()
                state = json.loads(row["state"]) if row else _fresh_state()
                initial = not state["bootstrapped"]
                for ts in valid_times:
                    if state["last_bar"] is None or ts > state["last_bar"]:
                        self._apply_bar(state, data[ts], ts, market)
                state["bootstrapped"] = True
                if initial:
                    state["forward_bars"] = 0
                db.execute("INSERT INTO policy(product,state) VALUES(?,?) "
                           "ON CONFLICT(product) DO UPDATE SET state=excluded.state",
                           (market, json.dumps(state, sort_keys=True)))
                mode = "live"
                ctxt = _features(state["recent_returns"])
                adopted = _choose(state, ctxt, explore=False, seed=0)
                target = _position(adopted, state["recent_returns"])
                enough = state["paper_bars"] >= MIN_HISTORY
                result = {"status": "READY" if enough else "WARMING_UP",
                          "mode": mode, "product": market,
                          "trade_effect": "ADOPTED_LONG_CASH" if enough else "NONE",
                          "learning_source": "completed_public_coinbase_5m_candles",
                          "learned_strategy": adopted, "target_exposure": target,
                          "allow_buy": bool(target),
                          "observed_through": state["last_bar"],
                          "reward_updates": sum(sum(v.values()) for v in state["visits"].values()),
                          "forward_bars": state["forward_bars"],
                          "paper_equity": round(state["equity"], 6),
                          "paper_drawdown": round(state["drawdown"], 6),
                          "paper_trades": state["mock_trades"],
                          "strategy_changes": state["strategy_changes"],
                          "note": "Executed mock rewards; no verified live P/L"}
                return result
        except Exception as exc:
            return {"status": "DATA_UNAVAILABLE", "mode": self.mode(),
                    "trade_effect": "NONE", "product": market,
                    "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}

    def status(self):
        with self._lock, self._db() as db:
            rows = db.execute("SELECT product,state FROM policy").fetchall()
            return {"mode": self.mode(),
                    "markets": [{ "product": row["product"],
                                  "forward_bars": json.loads(row["state"])["forward_bars"],
                                  "reward_updates": sum(sum(v.values()) for v in
                                       json.loads(row["state"])["visits"].values()),
                                  "paper_equity": json.loads(row["state"])["equity"]}
                                for row in rows]}
