from __future__ import annotations

import asyncio
import math
from typing import Protocol

from .core import BuiltInDecider, Decider, Decision, RiskGovernor, StateStore
from .game import score_game
from .wallets.cdp_wallet import SwapExecution, TradingSnapshot


class LiveWallet(Protocol):
    async def trading_snapshot(self) -> TradingSnapshot:
        ...

    async def swap_usdc_to_weth(self, fraction: float) -> SwapExecution:
        ...

    async def swap_weth_to_usdc(self, fraction: float) -> SwapExecution:
        ...


class LiveEngine:
    """Runs the existing decision/governor loop against the CDP wallet on Base."""

    def __init__(
        self,
        store: StateStore,
        wallet: LiveWallet,
        decider: Decider | None = None,
    ) -> None:
        self.store = store
        self.wallet = wallet
        self.decider: Decider = decider or BuiltInDecider()
        self._snapshot: dict | None = None

    def set_decider(self, decider: Decider) -> None:
        self.decider = decider

    def snapshot(self) -> dict:
        if self._snapshot is None:
            return self.sync()
        return self._snapshot

    def sync(self) -> dict:
        wallet_state = asyncio.run(self.wallet.trading_snapshot())
        price = float(wallet_state.weth_price_usdc)

        old_prices = self.store.prices()
        previous = old_prices.get("WETH", price)
        self.store.upsert_price("WETH", price, previous)

        start_text = self.store.get_meta("live_starting_value")
        if start_text is None:
            start = float(wallet_state.net_usdc)
            self.store.set_meta("live_starting_value", f"{start:.10f}")
        else:
            start = float(start_text)

        weth_qty = float(wallet_state.weth)
        positions = {}
        if weth_qty > 0.000000001:
            positions["WETH"] = {
                "qty": weth_qty,
                "avg_cost": price,
            }

        change = ((price / previous) - 1.0) if previous else 0.0
        net = float(wallet_state.net_usdc)
        cash = float(wallet_state.usdc)

        game = score_game(self.store, net, start)

        self._snapshot = {
            "cash": cash,
            "starting_cash": start,
            "positions": positions,
            "prices": {"WETH": price},
            "returns": {"WETH": change},
            "market_value": weth_qty * price,
            "net_liquidation": net,
            "pnl": net - start,
            "multiple": (net / start) if start else math.nan,
            "wallet_address": wallet_state.address,
            "network": wallet_state.network,
            "game": game.as_dict(),
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
