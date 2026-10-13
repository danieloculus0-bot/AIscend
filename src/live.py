from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Protocol

from .asset_universe import CoinbaseAssetUniverse
from .audit import AuditTrail
from .bean import BeanMemory
from .bean_policy import BeanPolicyGate
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
        asset_universe: CoinbaseAssetUniverse | None = None,
        bean: BeanMemory | None = None,
        audit: AuditTrail | None = None,
        policy_gate: BeanPolicyGate | None = None,
        growth_gate=None,
    ) -> None:
        self.store = store
        self.wallet = wallet
        self.researcher = researcher or MarketResearch()
        self.asset_universe = asset_universe or CoinbaseAssetUniverse()
        self.bean = bean or BeanMemory(self.store.conn)
        self.audit = audit or AuditTrail()
        self.decider: Decider = decider or ResearchDecider()
        self.policy_gate = policy_gate or BeanPolicyGate()
        self.growth_gate = growth_gate
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
        universe = self.asset_universe.collect()

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
        last_bean = float(self.store.get_meta("bean_last_observation") or "0")
        if time.time() - last_bean >= 55.0:
            self.bean.record_research(research)
            self.bean.record_universe(universe)

            bean_prices = {"ETH": price}
            for item in universe.get("top") or []:
                base = str(item.get("base") or "")
                asset_price = float(item.get("price") or 0.0)
                if base and asset_price > 0:
                    bean_prices[base] = asset_price
            self.bean.resolve_due(bean_prices)
            self.store.set_meta("bean_last_observation", str(time.time()))

        bean = self.bean.snapshot()

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
            "asset_universe": universe,
            "bean": bean,
            "bean_policy": json.loads(self.store.get_meta("bean_policy_latest") or "{}"),
            "bean_growth": json.loads(self.store.get_meta("bean_growth_latest") or "{}"),
        }
        return self._snapshot

    def step(self) -> dict:
        before = self.sync()
        raw = self.decider.decide(before)
        decision, status = RiskGovernor.normalize(raw, before)
        growth = self.growth_gate.assess("WETH") if self.growth_gate is not None else None
        if growth is not None:
            self.store.set_meta("bean_growth_latest", json.dumps(growth))
        adopted = bool(growth and growth.get("trade_effect") == "ADOPTED_LONG_CASH")
        if adopted:
            target = int(growth["target_exposure"])
            cash = float(before.get("cash") or 0)
            held = float((before.get("positions") or {}).get("WETH", {}).get("qty") or 0)
            rationale = "BEAN learned " + str(growth["learned_strategy"]) + " (executed mock rewards)."
            if target and cash > 0.000001:
                proposed = Decision("BUY", "WETH", 1.0, rationale)
            elif not target and held > 0:
                proposed = Decision("SELL", "WETH", 1.0, rationale)
            else:
                proposed = Decision("HOLD", None, 0.0, rationale)
            decision, status = RiskGovernor.normalize(proposed, before)
        else:
            try:
                policy = self.policy_gate.assess()
            except Exception as exc:
                policy = {"status": "DATA_UNAVAILABLE", "trade_effect": "NONE",
                          "reason": f"Policy evaluation failed: {type(exc).__name__}"}
            self.store.set_meta("bean_policy_latest", json.dumps(policy))
            # Historical Strategy School is advisory, not an independent
            # veto over the agent's funded, intentionally experimental trades.
            if status == "OK" and decision.action == "BUY" and policy.get("status") == "READY":
                exam = policy.get("experiment")
                target = policy.get("target") or "CASH"
                decision = Decision(decision.action, decision.symbol,
                                    decision.fraction,
                                    f"{decision.rationale} BEAN exam #{exam} advisory: {target}.")

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
            try:
                self.audit.record_trade(
                    venue="coinbase-cdp",
                    rail="base-smart-wallet",
                    network=str(getattr(self.wallet, "network", "base")),
                    side="BUY",
                    product_id=f"{execution.to_symbol}-{execution.from_symbol}",
                    base_asset=execution.to_symbol,
                    quote_asset=execution.from_symbol,
                    base_quantity=execution.to_amount,
                    quote_quantity=execution.from_amount,
                    unit_price_quote=price,
                    transaction_id=execution.user_op_hash,
                    status="complete",
                    rationale=decision.rationale,
                    source="wallet_balance_delta",
                    raw_provider_response={"user_op_hash": execution.user_op_hash},
                )
            except Exception as exc:
                self.store.add_ledger(
                    "AUDIT_ERROR",
                    execution.to_symbol,
                    0.0,
                    0.0,
                    0.0,
                    f"Base BUY executed but audit write failed: {exc}",
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
        try:
            self.audit.record_trade(
                venue="coinbase-cdp",
                rail="base-smart-wallet",
                network=str(getattr(self.wallet, "network", "base")),
                side="SELL",
                product_id=f"{execution.from_symbol}-{execution.to_symbol}",
                base_asset=execution.from_symbol,
                quote_asset=execution.to_symbol,
                base_quantity=execution.from_amount,
                quote_quantity=execution.to_amount,
                unit_price_quote=price,
                transaction_id=execution.user_op_hash,
                status="complete",
                rationale=decision.rationale,
                source="wallet_balance_delta",
                raw_provider_response={"user_op_hash": execution.user_op_hash},
            )
        except Exception as exc:
            self.store.add_ledger(
                "AUDIT_ERROR",
                execution.from_symbol,
                0.0,
                0.0,
                0.0,
                f"Base SELL executed but audit write failed: {exc}",
            )
