from __future__ import annotations

import asyncio
import math
from typing import Protocol

from .core import Decider, Decision, RiskGovernor, StateStore
from .game import score_game
from .research import MarketResearch, ResearchDecider
from .wallets.cdp_wallet import SwapExecution, TradingSnapshot


class LiveWallet(Protocol):
    async def trading_snapshot(self) -> TradingSnapshot:
        ...

    async def swap_usdc_to_weth(self, fraction: float) -> SwapExecution:
        ...

    async def swap_weth_to_usdc(self, fraction: float) -> SwapExecution:
        ...


class LiveEngine:
    """Runs the research/decision/governor loop against the CDP wallet on Base."""

    def __init__(
        self,
        store: StateStore,
        wallet: LiveWallet,
        decider: Decider | None = None,
        researcher: MarketResearch | None = None,
    ) -> None:
        self.store = store
        self.wallet = wallet
        self.researcher = researcher or MarketResearch()
        self.decider: Decider = decider or ResearchDecider()
        self._snapshot: dict | None = None

    def set_decider(self, decider: Decider) -> None:
        self.decider = decider

    def snapshot(self) -> dict:
        if self._snapshot is None:
            return self.sync()
        return self._snapshot

    def sync(self) -> dict:
        wallet_state = asyncio.run(self.wallet.trading_snapshot())
        research = self.researcher.collect().as_dict()

        price = float(wallet_state.weth_price_usdc)
        eth_research = research.get("eth") or {}
        if price <= 0 and eth_research:
            price = float(eth_research.get("price") or 0.0)

        old_prices = self.store.prices()
        previous = old_prices.get("WETH", price)
        if price > 0:
            self.store.upsert_price("WETH", price, previous if previous > 0 else price)

        weth_qty = float(wallet_state.weth)
        net = float(wallet_state.usdc) + (weth_qty * price)
        cash = float(wallet_state.usdc)

        # The actual funded wallet value establishes the game bankroll.
        # Merely connecting an empty wallet does not start the clock at $0.
        start_text = self.store.get_meta("live_starting_value")
        if start_text is None and net > 0.01:
            start = net
            self.store.set_meta("live_starting_value", f"{start:.10f}")
        elif start_text is not None:
            start = float(start_text)
        else:
            start = 0.0

        positions = {}
        if weth_qty > 0.000000001:
            positions["WETH"] = {
                "qty": weth_qty,
                "avg_cost": price,
            }

        change = ((price / previous) - 1.0) if previous and price else 0.0
        game = score_game(self.store, net, start) if start > 0 else {
            "points": 0,
            "multiple": 0.0,
            "elapsed_days": 0.0,
            "next_milestone": "2X / POINT 1",
            "next_milestone_multiple": 2.0,
            "level": 1,
            "level_target": 100000.0,
            "level_progress": 0.0,
            "level_complete": False,
        }

        self._snapshot = {
            "cash": cash,
            "starting_cash": start,
            "positions": positions,
            "prices": {"WETH": price},
            "returns": {"WETH": change},
            "market_value": weth_qty * price,
            "net_liquidation": net,
            "pnl": (net - start) if start else 0.0,
            "multiple": (net / start) if start else math.nan,
            "wallet_address": wallet_state.address,
            "network": wallet_state.network,
            "game": game.as_dict() if hasattr(game, "as_dict") else game,
            "research": research,
        }
        return self._snapshot

    def step(self) -> dict:
        before = self.sync()
        raw = self.decider.decide(before)
        decision, status = RiskGovernor.normalize(raw, before)

        execution: SwapExecution | None = None
        if status == "OK":
            if decision.action == "BUY":
                execution = asyncio.run(
                    self.wallet.swap_usdc_to_weth(decision.fraction)
                )
                status = "LIVE_EXECUTED"
            elif decision.action == "SELL":
                execution = asyncio.run(
                    self.wallet.swap_weth_to_usdc(decision.fraction)
                )
                status = "LIVE_EXECUTED"
            else:
                status = "HELD"

        if execution is not None:
            self._record_execution(execution, decision)

        self.store.add_decision(decision, status)
        return self.sync()

    def _record_execution(
        self,
        execution: SwapExecution,
        decision: Decision,
    ) -> None:
        if execution.from_symbol == "USDC":
            qty = float(execution.to_amount)
            spend = float(execution.from_amount)
            price = (spend / qty) if qty else 0.0
            self.store.add_ledger(
                "LIVE_BUY",
                execution.to_symbol,
                qty,
                price,
                -spend,
                f"{decision.rationale} | user_op={execution.user_op_hash}",
            )
            return

        qty = float(execution.from_amount)
        proceeds = float(execution.to_amount)
        price = (proceeds / qty) if qty else 0.0
        self.store.add_ledger(
            "LIVE_SELL",
            execution.from_symbol,
            qty,
            price,
            proceeds,
            f"{decision.rationale} | user_op={execution.user_op_hash}",
        )
