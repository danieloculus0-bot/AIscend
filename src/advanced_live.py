from __future__ import annotations

import math
import time
import uuid
from decimal import Decimal
from typing import Any

from .asset_universe import CoinbaseAssetUniverse
from .audit import AuditTrail
from .bean import BeanMemory
from .core import Decision, StateStore
from .game import score_game
from .wallets.advanced_trade import AdvancedTradeSpot


QUOTE_ASSETS = {"USD", "USDC"}


class AdvancedSpotEngine:
    """
    Autonomous multi-asset Coinbase Advanced spot engine.

    Use this with a dedicated Coinbase Advanced portfolio/API key. It ranks the
    same rotating Coinbase universe used by AIscend research, buys the strongest
    eligible long setup using available USD/USDC, and exits held assets when
    their signal deteriorates. No borrowing or shorting is used.
    """

    def __init__(
        self,
        store: StateStore,
        rail: AdvancedTradeSpot | None = None,
        asset_universe: CoinbaseAssetUniverse | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self.store = store
        self.rail = rail or AdvancedTradeSpot()
        self.asset_universe = asset_universe or CoinbaseAssetUniverse()
        self.audit = audit or AuditTrail()
        self.bean = BeanMemory(self.store.conn)
        self._snapshot: dict[str, Any] | None = None

    @staticmethod
    def _split_product(product_id: str) -> tuple[str, str]:
        base, _, quote = product_id.upper().rpartition("-")
        return base, quote

    def _product_for_currency(
        self,
        currency: str,
        product_ids: tuple[str, ...],
        preferred_quote: str | None = None,
    ) -> str | None:
        currency = currency.upper()
        candidates = [
            product_id
            for product_id in product_ids
            if self._split_product(product_id)[0] == currency
            and self._split_product(product_id)[1] in QUOTE_ASSETS
        ]
        if preferred_quote:
            for product_id in candidates:
                if self._split_product(product_id)[1] == preferred_quote:
                    return product_id
        for quote in ("USDC", "USD"):
            for product_id in candidates:
                if self._split_product(product_id)[1] == quote:
                    return product_id
        return candidates[0] if candidates else None

    def sync(self) -> dict[str, Any]:
        status = self.rail.status()
        universe = self.asset_universe.collect()
        product_ids = tuple(status.product_ids)

        raw_balances = {
            item.currency: float(item.available)
            for item in status.balances
            if float(item.available) > 0.000000001
        }
        cash_by_quote = {
            quote: raw_balances.get(quote, 0.0)
            for quote in QUOTE_ASSETS
        }
        cash = sum(cash_by_quote.values())

        universe_by_product = {
            str(item.get("product") or "").upper(): item
            for item in universe.get("top") or []
            if item.get("product")
        }

        positions: dict[str, dict[str, float | str]] = {}
        prices: dict[str, float] = {}
        market_value = 0.0

        for currency, qty in raw_balances.items():
            if currency in QUOTE_ASSETS:
                continue
            product_id = self._product_for_currency(currency, product_ids)
            if not product_id:
                continue

            item = universe_by_product.get(product_id)
            price = float(item.get("price") or 0.0) if item else 0.0
            if price <= 0:
                try:
                    product = self.rail.product(product_id)
                    price = float(product.get("price") or 0.0)
                except Exception:
                    price = 0.0
            if price <= 0:
                continue

            value = qty * price
            market_value += value
            positions[product_id] = {
                "qty": qty,
                "avg_cost": price,
                "base": currency,
            }
            prices[product_id] = price

        net = cash + market_value
        start_text = self.store.get_meta("advanced_starting_value")
        if start_text is None and net > 0.01:
            start = net
            self.store.set_meta("advanced_starting_value", f"{start:.10f}")
        elif start_text is not None:
            start = float(start_text)
        else:
            start = 0.0

        if time.time() - float(self.store.get_meta("bean_advanced_last_observation") or "0") >= 55.0:
            self.bean.record_universe(universe)
            bean_prices = {}
            for item in universe.get("top") or []:
                base = str(item.get("base") or "")
                price = float(item.get("price") or 0.0)
                if base and price > 0:
                    bean_prices[base] = price
            self.bean.resolve_due(bean_prices)
            self.store.set_meta("bean_advanced_last_observation", str(time.time()))

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

        radar = self._radar(universe, product_ids, cash_by_quote, positions)

        self._snapshot = {
            "cash": cash,
            "cash_by_quote": cash_by_quote,
            "starting_cash": start,
            "positions": positions,
            "prices": prices,
            "returns": {
                product: float(universe_by_product.get(product, {}).get("return_1h") or 0.0)
                for product in positions
            },
            "market_value": market_value,
            "net_liquidation": net,
            "pnl": (net - start) if start else 0.0,
            "multiple": (net / start) if start else math.nan,
            "wallet_address": "Coinbase Advanced portfolio",
            "network": "coinbase-advanced",
            "game": game.as_dict() if hasattr(game, "as_dict") else game,
            "research": {
                "available": bool(universe.get("available")),
                "prediction": radar.get("prediction", "NEUTRAL"),
                "confidence": radar.get("confidence", 0.0),
                "composite_score": radar.get("score", 0.0),
                "thesis": radar.get("thesis", "Scanning Coinbase spot universe."),
                "human": {},
            },
            "asset_universe": universe,
            "bean": self.bean.snapshot(),
            "market_radar": radar,
            "execution_rail": "coinbase-advanced",
        }
        return self._snapshot

    def _radar(
        self,
        universe: dict[str, Any],
        product_ids: tuple[str, ...],
        cash_by_quote: dict[str, float],
        positions: dict[str, dict[str, float | str]],
    ) -> dict[str, Any]:
        tradable = set(product_ids)
        rows = [
            item
            for item in (universe.get("top") or [])
            if str(item.get("product") or "").upper() in tradable
        ]
        longs = sorted(rows, key=lambda item: float(item.get("score") or 0.0), reverse=True)
        funded_longs = [
            item
            for item in longs
            if cash_by_quote.get(
                self._split_product(str(item.get("product") or ""))[1],
                0.0,
            ) > 0.50
        ]
        best = funded_longs[0] if funded_longs else (longs[0] if longs else None)
        held = []
        for product_id in positions:
            item = next(
                (
                    row
                    for row in rows
                    if str(row.get("product") or "").upper() == product_id
                ),
                None,
            )
            if item:
                held.append(item)
        weakest_held = (
            min(held, key=lambda item: float(item.get("score") or 0.0))
            if held
            else None
        )

        score = float(best.get("score") or 0.0) if best else 0.0
        confidence = min(0.95, 0.35 + abs(score) * 0.60) if best else 0.0
        prediction = "BULLISH" if score >= 0.16 else "BEARISH" if score <= -0.16 else "NEUTRAL"
        if best:
            product = str(best.get("product") or "")
            quote = self._split_product(product)[1]
            funded = cash_by_quote.get(quote, 0.0) > 0.50
            thesis = (
                f"Best {'funded ' if funded else ''}tradable spot candidate {product}: "
                f"score {score:+.3f}, 1h {float(best.get('return_1h') or 0):+.2%}, "
                f"6h {float(best.get('return_6h') or 0):+.2%}, "
                f"volume {float(best.get('volume_ratio') or 0):.2f}x; "
                f"{quote} available {cash_by_quote.get(quote, 0.0):.2f}."
            )
        else:
            thesis = "No currently-scored Coinbase product is tradable for this Advanced account."

        return {
            "best_long": best,
            "weakest_held": weakest_held,
            "score": score,
            "confidence": confidence,
            "prediction": prediction,
            "thesis": thesis,
        }

    def step(self) -> dict[str, Any]:
        before = self.sync()
        radar = before.get("market_radar") or {}
        best = radar.get("best_long")
        weakest = radar.get("weakest_held")

        decision = Decision("HOLD", None, 0.0, "No Coinbase spot trade clears the current threshold.")
        status = "HELD"

        if weakest is not None:
            held_product = str(weakest.get("product") or "").upper()
            held_score = float(weakest.get("score") or 0.0)
            if held_score <= -0.14 and held_product in before["positions"]:
                fraction = min(1.0, max(0.35, 0.35 + abs(held_score) * 0.65))
                decision = Decision(
                    "SELL",
                    held_product,
                    fraction,
                    f"Coinbase-wide signal deteriorated to {held_score:+.3f}.",
                )
                self._execute_sell(decision, before)
                status = "LIVE_EXECUTED"

        if status == "HELD" and best is not None:
            product_id = str(best.get("product") or "").upper()
            score = float(best.get("score") or 0.0)
            spread_bps = float(best.get("spread_bps") or 0.0)
            volume_ratio = float(best.get("volume_ratio") or 0.0)
            _, quote = self._split_product(product_id)
            available_quote = float((before.get("cash_by_quote") or {}).get(quote, 0.0))

            if (
                score >= 0.18
                and spread_bps <= 35.0
                and volume_ratio >= 0.40
                and available_quote > 0.50
            ):
                fraction = min(0.85, max(0.20, 0.20 + abs(score) * 0.65))
                decision = Decision(
                    "BUY",
                    product_id,
                    fraction,
                    (
                        f"Best Coinbase-wide executable spot setup at {score:+.3f}; "
                        f"spread {spread_bps:.1f} bps, volume {volume_ratio:.2f}x."
                    ),
                )
                self._execute_buy(decision, before)
                status = "LIVE_EXECUTED"

        self.store.add_decision(decision, status)
        return self.sync()

    def _execute_buy(self, decision: Decision, snapshot: dict[str, Any]) -> None:
        product_id = str(decision.symbol)
        _, quote = self._split_product(product_id)
        available = Decimal(str((snapshot.get("cash_by_quote") or {}).get(quote, 0.0)))
        spend = available * Decimal(str(decision.fraction))
        if spend <= Decimal("0.50"):
            raise RuntimeError(f"Insufficient {quote} for Coinbase Advanced buy.")

        preview = self.rail.preview_market_buy(product_id, spend)
        client_order_id = f"aiscend-{uuid.uuid4()}"
        order = self.rail.market_buy(
            product_id,
            spend,
            client_order_id=client_order_id,
        )
        self.store.add_ledger(
            "ADVANCED_BUY",
            product_id,
            0.0,
            0.0,
            -float(spend),
            f"{decision.rationale} | preview={str(preview)[:220]} | order={str(order)[:220]}",
        )

        base, quote = self._split_product(product_id)
        estimated_base = self.audit.provider_id(
            preview,
            "base_size",
            "estimated_base_size",
            "base_size_total",
        )
        unit_price = None
        if estimated_base:
            try:
                unit_price = spend / Decimal(estimated_base)
            except Exception:
                unit_price = None
        try:
            self.audit.record_trade(
                venue="coinbase",
                rail="coinbase-advanced",
                network="coinbase",
                side="BUY",
                product_id=product_id,
                base_asset=base,
                quote_asset=quote,
                base_quantity=estimated_base or None,
                quote_quantity=spend,
                unit_price_quote=unit_price,
                order_id=self.audit.provider_id(order, "order_id", "orderId"),
                client_order_id=client_order_id,
                status="provider_response",
                rationale=decision.rationale,
                source="coinbase_advanced_order_response",
                raw_provider_response={"preview": preview, "order": order},
            )
        except Exception as exc:
            self.store.add_ledger(
                "AUDIT_ERROR",
                product_id,
                0.0,
                0.0,
                0.0,
                f"Advanced BUY executed but audit write failed: {exc}",
            )

    def _execute_sell(self, decision: Decision, snapshot: dict[str, Any]) -> None:
        product_id = str(decision.symbol)
        position = (snapshot.get("positions") or {}).get(product_id) or {}
        qty = Decimal(str(position.get("qty") or 0.0)) * Decimal(str(decision.fraction))
        if qty <= 0:
            raise RuntimeError(f"No {product_id} position available to sell.")

        client_order_id = f"aiscend-{uuid.uuid4()}"
        order = self.rail.market_sell(
            product_id,
            qty,
            client_order_id=client_order_id,
        )
        self.store.add_ledger(
            "ADVANCED_SELL",
            product_id,
            float(qty),
            0.0,
            0.0,
            f"{decision.rationale} | order={str(order)[:220]}",
        )

        base, quote = self._split_product(product_id)
        try:
            self.audit.record_trade(
                venue="coinbase",
                rail="coinbase-advanced",
                network="coinbase",
                side="SELL",
                product_id=product_id,
                base_asset=base,
                quote_asset=quote,
                base_quantity=qty,
                order_id=self.audit.provider_id(order, "order_id", "orderId"),
                client_order_id=client_order_id,
                status="provider_response",
                rationale=decision.rationale,
                source="coinbase_advanced_order_response",
                raw_provider_response=order,
            )
        except Exception as exc:
            self.store.add_ledger(
                "AUDIT_ERROR",
                product_id,
                0.0,
                0.0,
                0.0,
                f"Advanced SELL executed but audit write failed: {exc}",
            )
