from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Decision:
    action: str
    symbol: str | None
    fraction: float
    rationale: str


class Decider(Protocol):
    def decide(self, snapshot: dict[str, Any]) -> Decision:
        ...


def app_data_dir() -> Path:
    root = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home())
    path = Path(root) / "AutonomousCapitalLab"
    path.mkdir(parents=True, exist_ok=True)
    return path


class StateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "state.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.conn.close()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                qty REAL NOT NULL,
                avg_cost REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market (
                symbol TEXT PRIMARY KEY,
                price REAL NOT NULL,
                prev_price REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                action TEXT NOT NULL,
                symbol TEXT,
                fraction REAL NOT NULL,
                rationale TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                symbol TEXT,
                qty REAL NOT NULL DEFAULT 0,
                price REAL NOT NULL DEFAULT 0,
                cash_delta REAL NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self.conn.commit()

    def initialized(self) -> bool:
        return self.get_meta("starting_cash") is not None

    def initialize(self, starting_cash: float = 10.0) -> None:
        if self.initialized():
            return
        starting_cash = max(0.01, float(starting_cash))
        self.set_meta("starting_cash", f"{starting_cash:.8f}")
        self.set_meta("cash", f"{starting_cash:.8f}")
        self.set_meta("created_at", str(time.time()))
        self.add_ledger("SEED", None, 0, 0, starting_cash, "Initial bankroll")

    def reset(self, starting_cash: float = 10.0) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM meta")
            self.conn.execute("DELETE FROM positions")
            self.conn.execute("DELETE FROM market")
            self.conn.execute("DELETE FROM price_history")
            self.conn.execute("DELETE FROM decisions")
            self.conn.execute("DELETE FROM ledger")
        self.initialize(starting_cash)

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    @property
    def cash(self) -> float:
        return float(self.get_meta("cash") or "0")

    @cash.setter
    def cash(self, value: float) -> None:
        self.set_meta("cash", f"{max(0.0, value):.10f}")

    @property
    def starting_cash(self) -> float:
        return float(self.get_meta("starting_cash") or "0")

    def positions(self) -> dict[str, dict[str, float]]:
        rows = self.conn.execute(
            "SELECT symbol, qty, avg_cost FROM positions WHERE qty > 0.000000001"
        ).fetchall()
        return {
            str(r["symbol"]): {"qty": float(r["qty"]), "avg_cost": float(r["avg_cost"])}
            for r in rows
        }

    def position_qty(self, symbol: str) -> float:
        row = self.conn.execute(
            "SELECT qty FROM positions WHERE symbol=?", (symbol,)
        ).fetchone()
        return float(row["qty"]) if row else 0.0

    def set_position(self, symbol: str, qty: float, avg_cost: float) -> None:
        with self.conn:
            if qty <= 0.000000001:
                self.conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            else:
                self.conn.execute(
                    "INSERT INTO positions(symbol,qty,avg_cost) VALUES(?,?,?) "
                    "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_cost=excluded.avg_cost",
                    (symbol, qty, avg_cost),
                )

    def prices(self) -> dict[str, float]:
        rows = self.conn.execute("SELECT symbol, price FROM market").fetchall()
        return {str(r["symbol"]): float(r["price"]) for r in rows}

    def previous_prices(self) -> dict[str, float]:
        rows = self.conn.execute("SELECT symbol, prev_price FROM market").fetchall()
        return {str(r["symbol"]): float(r["prev_price"]) for r in rows}

    def upsert_price(self, symbol: str, price: float, previous: float) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute(
                "INSERT INTO market(symbol,price,prev_price) VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET price=excluded.price, prev_price=excluded.prev_price",
                (symbol, price, previous),
            )
            self.conn.execute(
                "INSERT INTO price_history(ts,symbol,price) VALUES(?,?,?)",
                (now, symbol, price),
            )
            self.conn.execute(
                "DELETE FROM price_history WHERE id IN ("
                "SELECT id FROM price_history ORDER BY id DESC LIMIT -1 OFFSET 2000)"
            )

    def recent_prices(self, symbol: str, limit: int = 12) -> list[float]:
        rows = self.conn.execute(
            "SELECT price FROM price_history WHERE symbol=? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        return [float(r["price"]) for r in reversed(rows)]

    def add_decision(self, decision: Decision, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO decisions(ts,action,symbol,fraction,rationale,status) "
                "VALUES(?,?,?,?,?,?)",
                (
                    time.time(),
                    decision.action,
                    decision.symbol,
                    decision.fraction,
                    decision.rationale,
                    status,
                ),
            )

    def add_ledger(
        self,
        kind: str,
        symbol: str | None,
        qty: float,
        price: float,
        cash_delta: float,
        note: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO ledger(ts,kind,symbol,qty,price,cash_delta,note) "
                "VALUES(?,?,?,?,?,?,?)",
                (time.time(), kind, symbol, qty, price, cash_delta, note),
            )

    def latest_decisions(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def latest_ledger(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


class SyntheticMarket:
    """A persistent simulated market used to validate the autonomous loop end-to-end."""

    STARTING_PRICES = {
        "CLIP": 1.20,
        "BYTE": 2.40,
        "GEAR": 4.20,
        "NOVA": 7.50,
        "SPRK": 0.65,
    }

    def __init__(self, store: StateStore) -> None:
        self.store = store
        self.rng = random.Random()
        if not self.store.prices():
            for symbol, price in self.STARTING_PRICES.items():
                self.store.upsert_price(symbol, price, price)

    def tick(self) -> dict[str, float]:
        current = self.store.prices()
        for index, (symbol, old) in enumerate(sorted(current.items())):
            history = self.store.recent_prices(symbol, 8)
            recent = 0.0
            if len(history) >= 2 and history[-2] > 0:
                recent = (history[-1] - history[-2]) / history[-2]

            structural_drift = (index - 2) * 0.0007
            momentum = max(-0.015, min(0.015, recent * 0.28))
            shock = self.rng.gauss(0, 0.022 + index * 0.004)
            change = max(-0.12, min(0.12, structural_drift + momentum + shock))
            new_price = max(0.05, old * (1.0 + change))
            self.store.upsert_price(symbol, new_price, old)
        return self.store.prices()


class BuiltInDecider:
    """Aggressive autonomous strategy intended for the $10 experiment."""

    def __init__(self) -> None:
        self.rng = random.Random()

    def decide(self, snapshot: dict[str, Any]) -> Decision:
        cash = float(snapshot["cash"])
        positions = snapshot["positions"]
        returns = snapshot["returns"]

        held_ranked = sorted(
            (
                (returns.get(symbol, 0.0), symbol)
                for symbol in positions
                if positions[symbol]["qty"] > 0
            )
        )
        if held_ranked:
            worst_return, worst_symbol = held_ranked[0]
            if worst_return < -0.012:
                frac = min(1.0, 0.55 + abs(worst_return) * 8)
                return Decision(
                    "SELL",
                    worst_symbol,
                    frac,
                    f"Cutting weakening position after {worst_return:+.2%} move.",
                )

        ranked = sorted(
            ((value, symbol) for symbol, value in returns.items()), reverse=True
        )
        if cash >= 0.05 and ranked:
            best_return, best_symbol = ranked[0]
            if best_return > 0.002 or not positions:
                conviction = min(1.0, max(0.35, 0.55 + best_return * 8))
                if self.rng.random() < 0.08:
                    best_symbol = self.rng.choice(list(returns.keys()))
                    conviction = 0.30
                    reason = "Exploration trade to avoid locking into one market regime."
                else:
                    reason = f"Deploying cash into strongest current momentum ({best_return:+.2%})."
                return Decision("BUY", best_symbol, conviction, reason)

        if positions and cash < 0.05 and self.rng.random() < 0.12:
            symbol = self.rng.choice(list(positions.keys()))
            return Decision("SELL", symbol, 0.25, "Releasing liquidity for future opportunities.")

        return Decision("HOLD", None, 0.0, "No trade clears the current opportunity threshold.")


class OpenAICompatibleDecider:
    """
    Optional autonomous LLM decider.

    Endpoint should be an OpenAI-compatible /v1/chat/completions URL.
    The key is read from AUTOCAPITAL_API_KEY and is never stored in the database.
    """

    def __init__(self, endpoint: str, model: str) -> None:
        self.endpoint = endpoint
        self.model = model

    def decide(self, snapshot: dict[str, Any]) -> Decision:
        compact = {
            "cash": snapshot["cash"],
            "starting_cash": snapshot["starting_cash"],
            "net_liquidation": snapshot["net_liquidation"],
            "positions": snapshot["positions"],
            "prices": snapshot["prices"],
            "returns": snapshot["returns"],
            "game": snapshot.get("game"),
        }
        prompt = (
            "You are the autonomous decision engine for AIscend, a capital-growth game. "
            "Your objective is to maximize game points by increasing net liquidation value "
            "as quickly as possible. Every completed doubling of the bankroll is worth exactly "
            "ONE point. Speed is tracked separately through time-to-milestone, so when two paths "
            "reach the same point level, prefer the one that gets there sooner and preserves more "
            "net liquidation value. LEVEL 1 has a mandatory success measure: grow the bankroll to "
            "$100,000 net liquidation value. The game is not won before that threshold is reached. "
            "Once Level 1 is complete, preserve the result and continue compounding for later levels. "
            "HOLD is valid when it improves expected score; pointless churn is not rewarded. "
            "You may BUY, SELL, or HOLD without asking the human for approval. No borrowing "
            "is available. Return ONLY JSON with "
            'keys action, symbol, fraction, rationale. "fraction" must be 0..1 and means the '
            "fraction of available cash to spend for BUY or the fraction of the held position "
            "to liquidate for SELL. HOLD uses null symbol and 0 fraction. Current state: "
            + json.dumps(compact, separators=(",", ":"))
        )
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0.25,
                "messages": [
                    {"role": "system", "content": "Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        key = os.getenv("AUTOCAPITAL_API_KEY", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            if content.startswith("json"):
                content = content[4:].strip()
        data = json.loads(content)
        action = str(data.get("action", "HOLD")).upper()
        symbol = data.get("symbol")
        fraction = float(data.get("fraction", 0.0))
        rationale = str(data.get("rationale", "AI decision"))
        return Decision(action, symbol, fraction, rationale)


class RiskGovernor:
    """Non-negotiable account boundary. The agent may use its bankroll, but not money outside it."""

    @staticmethod
    def normalize(decision: Decision, snapshot: dict[str, Any]) -> tuple[Decision, str]:
        action = decision.action.upper().strip()
        symbol = decision.symbol.upper().strip() if decision.symbol else None
        fraction = max(0.0, min(1.0, float(decision.fraction)))
        normalized = Decision(action, symbol, fraction, decision.rationale[:500])

        if action == "HOLD":
            return Decision("HOLD", None, 0.0, normalized.rationale), "OK"
        if action not in {"BUY", "SELL"}:
            return Decision("HOLD", None, 0.0, "Rejected invalid action."), "REJECTED_ACTION"
        if not symbol or symbol not in snapshot["prices"]:
            return Decision("HOLD", None, 0.0, "Rejected unknown asset."), "REJECTED_SYMBOL"
        if fraction <= 0:
            return Decision("HOLD", None, 0.0, "Rejected zero-size trade."), "REJECTED_SIZE"
        if action == "BUY" and snapshot["cash"] <= 0.000001:
            return Decision("HOLD", None, 0.0, "No cash available."), "REJECTED_CASH"
        if action == "SELL" and snapshot["positions"].get(symbol, {}).get("qty", 0.0) <= 0:
            return Decision("HOLD", None, 0.0, "No position available to sell."), "REJECTED_POSITION"
        return normalized, "OK"


class Engine:
    def __init__(
        self,
        store: StateStore,
        decider: Decider | None = None,
    ) -> None:
        self.store = store
        if not self.store.initialized():
            self.store.initialize(10.0)
        self.market = SyntheticMarket(store)
        self.decider: Decider = decider or BuiltInDecider()

    def set_decider(self, decider: Decider) -> None:
        self.decider = decider

    def snapshot(self) -> dict[str, Any]:
        prices = self.store.prices()
        previous = self.store.previous_prices()
        positions = self.store.positions()
        returns = {
            symbol: ((price / previous[symbol]) - 1.0) if previous.get(symbol, 0) else 0.0
            for symbol, price in prices.items()
        }
        market_value = sum(
            p["qty"] * prices.get(symbol, 0.0) for symbol, p in positions.items()
        )
        net = self.store.cash + market_value
        return {
            "cash": self.store.cash,
            "starting_cash": self.store.starting_cash,
            "positions": positions,
            "prices": prices,
            "returns": returns,
            "market_value": market_value,
            "net_liquidation": net,
            "pnl": net - self.store.starting_cash,
            "multiple": (net / self.store.starting_cash) if self.store.starting_cash else math.nan,
        }

    def step(self) -> dict[str, Any]:
        self.market.tick()
        before = self.snapshot()
        raw = self.decider.decide(before)
        decision, status = RiskGovernor.normalize(raw, before)
        if status == "OK":
            status = self._execute(decision, before)
        self.store.add_decision(decision, status)
        return self.snapshot()

    def _execute(self, decision: Decision, snapshot: dict[str, Any]) -> str:
        if decision.action == "HOLD":
            return "HELD"

        symbol = str(decision.symbol)
        price = float(snapshot["prices"][symbol])

        if decision.action == "BUY":
            spend = self.store.cash * decision.fraction
            if spend < 0.000001:
                return "SKIPPED_DUST"
            qty = spend / price
            old = self.store.positions().get(symbol, {"qty": 0.0, "avg_cost": 0.0})
            old_qty = float(old["qty"])
            old_cost = float(old["avg_cost"])
            new_qty = old_qty + qty
            new_avg = ((old_qty * old_cost) + (qty * price)) / new_qty
            self.store.cash = self.store.cash - spend
            self.store.set_position(symbol, new_qty, new_avg)
            self.store.add_ledger(
                "BUY", symbol, qty, price, -spend, decision.rationale
            )
            return "EXECUTED"

        qty_held = self.store.position_qty(symbol)
        qty = qty_held * decision.fraction
        if qty < 0.000000001:
            return "SKIPPED_DUST"
        proceeds = qty * price
        old = self.store.positions()[symbol]
        self.store.set_position(symbol, qty_held - qty, float(old["avg_cost"]))
        self.store.cash = self.store.cash + proceeds
        self.store.add_ledger(
            "SELL", symbol, qty, price, proceeds, decision.rationale
        )
        return "EXECUTED"
