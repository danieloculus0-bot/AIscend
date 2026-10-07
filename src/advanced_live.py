from __future__ import annotations

import math
import time
import uuid
from decimal import Decimal
from typing import Any

from .asset_universe import CoinbaseAssetUniverse
from .bean import BeanMemory
from .core import Decision, StateStore
from .game import score_game
from .listing_sentinel import CoinbaseListingSentinel
from .wallets.advanced_trade import AdvancedTradeSpot


DOLLAR_ASSETS = {"USD", "USDC"}


class AdvancedSpotEngine:
    """
    Autonomous multi-asset Coinbase Advanced spot engine.

    Use this with a dedicated Coinbase Advanced portfolio/API key. It ranks the
    same rotating Coinbase universe used by AIscend research, buys the strongest
    eligible long setup using whatever quote asset the account actually holds,
    and exits held assets when their signal deteriorates. There is no coin
    allowlist: account tradability and market conditions decide eligibility.
    No borrowing or shorting is used.
    """

    def __init__(
        self,
        store: StateStore,
        rail: AdvancedTradeSpot | None = None,
        asset_universe: CoinbaseAssetUniverse | None = None,
        listing_sentinel: CoinbaseListingSentinel | None = None,
    ) -> None:
        self.store = store
        self.rail = rail or AdvancedTradeSpot()
        self.asset_universe = asset_universe or CoinbaseAssetUniverse()
        universe_fetch = getattr(self.asset_universe, "_get_json", None)
        self.listing_sentinel = listing_sentinel or CoinbaseListingSentinel(
            self.store,
            fetch_json=universe_fetch,
        )
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

    def _product_price(
        self,
        product_id: str,
        universe_by_product: dict[str, dict[str, Any]],
    ) -> float:
        item = universe_by_product.get(product_id.upper())
        if item is not None:
            price = float(item.get("price") or 0.0)
            if price > 0:
                return price
        try:
            product = self.rail.product(product_id)
            return float(product.get("price") or 0.0)
        except Exception:
            return 0.0

    def _usd_rate(
        self,
        currency: str,
        product_ids: tuple[str, ...],
        universe_by_product: dict[str, dict[str, Any]],
        *,
        visited: set[str] | None = None,
        depth: int = 0,
    ) -> float:
        currency = currency.upper()
        if currency in DOLLAR_ASSETS:
            return 1.0
        if depth > 2:
            return 0.0

        seen = set(visited or ())
        if currency in seen:
            return 0.0
        seen.add(currency)

        # Prefer direct dollar markets when they exist.
        for dollar in ("USDC", "USD"):
            direct = f"{currency}-{dollar}"
            if direct in product_ids:
                price = self._product_price(direct, universe_by_product)
                if price > 0:
                    return price

            inverse = f"{dollar}-{currency}"
            if inverse in product_ids:
                price = self._product_price(inverse, universe_by_product)
                if price > 0:
                    return 1.0 / price

        # Fall back to a short cross-rate path through another Coinbase quote.
        # Only inspect products touching this currency; do not turn valuation into
        # a full-catalog REST request storm.
        neighbors = []
        for product_id in product_ids:
            base, quote = self._split_product(product_id)
            if base == currency or quote == currency:
                neighbors.append((product_id, base, quote))

        bridge_priority = {"BTC": 0, "ETH": 1, "USDT": 2, "EUR": 3, "GBP": 4}
        neighbors.sort(
            key=lambda row: bridge_priority.get(
                row[2] if row[1] == currency else row[1],
                99,
            )
        )

        for product_id, base, quote in neighbors[:12]:
            price = self._product_price(product_id, universe_by_product)
            if price <= 0:
                continue
            if base == currency and quote not in seen:
                quote_rate = self._usd_rate(
                    quote,
                    product_ids,
                    universe_by_product,
                    visited=seen,
                    depth=depth + 1,
                )
                if quote_rate > 0:
                    return price * quote_rate
            if quote == currency and base not in seen:
                base_rate = self._usd_rate(
                    base,
                    product_ids,
                    universe_by_product,
                    visited=seen,
                    depth=depth + 1,
                )
                if base_rate > 0:
                    return base_rate / price
        return 0.0

    def sync(self) -> dict[str, Any]:
        status = self.rail.status()
        listing = self.listing_sentinel.poll()
        priority_products = [
            str(event.get("product") or "").upper()
            for event in (
                list(listing.get("new_products") or [])
                + list(listing.get("changes") or [])
            )
            if event.get("product")
        ]
        universe = self.asset_universe.collect(
            priority_products=priority_products,
        )
        product_ids = tuple(status.product_ids)

        raw_balances = {
            item.currency: float(item.available)
            for item in status.balances
            if float(item.available) > 0.000000001
        }
        quote_currencies = {
            self._split_product(product_id)[1]
            for product_id in product_ids
            if self._split_product(product_id)[1]
        }
        cash_by_quote = {
            quote: raw_balances.get(quote, 0.0)
            for quote in quote_currencies
            if raw_balances.get(quote, 0.0) > 0.000000001
        }

        # Preserve the game's dollar-denominated bankroll accounting while
        # allowing execution to use any funded quote currency.
        cash = sum(raw_balances.get(asset, 0.0) for asset in DOLLAR_ASSETS)

        universe_by_product = {
            str(item.get("product") or "").upper(): item
            for item in universe.get("top") or []
            if item.get("product")
        }

        positions: dict[str, dict[str, float | str]] = {}
        prices: dict[str, float] = {}
        balance_values_usd: dict[str, float] = {}

        for currency, qty in raw_balances.items():
            usd_rate = self._usd_rate(
                currency,
                product_ids,
                universe_by_product,
            )
            if usd_rate > 0:
                balance_values_usd[currency] = qty * usd_rate

            if currency in DOLLAR_ASSETS:
                continue

            product_id = self._product_for_currency(currency, product_ids)
            if not product_id or usd_rate <= 0:
                continue

            positions[product_id] = {
                "qty": qty,
                "avg_cost": usd_rate,
                "base": currency,
            }
            prices[product_id] = usd_rate

        net = sum(balance_values_usd.values())
        market_value = max(0.0, net - cash)
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

        radar = self._radar(
            universe,
            product_ids,
            cash_by_quote,
            positions,
            listing,
        )

        self._snapshot = {
            "cash": cash,
            "cash_by_quote": cash_by_quote,
            "balance_values_usd": balance_values_usd,
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
            "listing_sentinel": listing,
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
        listing: dict[str, Any],
    ) -> dict[str, Any]:
        tradable = set(product_ids)
        hot_by_product = {
            str(event.get("product") or "").upper(): event
            for event in (listing.get("hot") or [])
            if str(event.get("stage") or "") == "FULL_TRADING"
        }

        rows: list[dict[str, Any]] = []
        now = time.time()
        for source in universe.get("top") or []:
            product = str(source.get("product") or "").upper()
            if product not in tradable:
                continue
            item = dict(source)
            event = hot_by_product.get(product)
            raw_score = float(item.get("score") or 0.0)
            listing_boost = 0.0
            if event is not None:
                age = max(0.0, now - float(event.get("ts") or now))
                # A public listing/trading-enable event gets a temporary momentum
                # premium that decays to zero over six hours. Liquidity gates still
                # apply before any order can be attempted.
                listing_boost = max(0.0, 0.35 * (1.0 - age / 21600.0))
                item["listing_event"] = event
            item["listing_boost"] = listing_boost
            item["decision_score"] = max(-1.0, min(1.0, raw_score + listing_boost))
            rows.append(item)

        longs = sorted(
            rows,
            key=lambda item: float(item.get("decision_score") or 0.0),
            reverse=True,
        )
        funded_longs = [
            item
            for item in longs
            if cash_by_quote.get(
                self._split_product(str(item.get("product") or ""))[1],
                0.0,
            ) > 0.0
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

        score = float(best.get("decision_score") or best.get("score") or 0.0) if best else 0.0
        confidence = min(0.95, 0.35 + abs(score) * 0.60) if best else 0.0
        prediction = "BULLISH" if score >= 0.16 else "BEARISH" if score <= -0.16 else "NEUTRAL"
        if best:
            product = str(best.get("product") or "")
            quote = self._split_product(product)[1]
            funded = cash_by_quote.get(quote, 0.0) > 0.0
            listing_event = best.get("listing_event")
            listing_note = ""
            if isinstance(listing_event, dict):
                listing_note = (
                    f" Public Coinbase listing signal {listing_event.get('event')} "
                    f"at stage {listing_event.get('stage')}."
                )
            thesis = (
                f"Best {'funded ' if funded else ''}tradable spot candidate {product}: "
                f"decision score {score:+.3f}, raw {float(best.get('score') or 0):+.3f}, "
                f"1h {float(best.get('return_1h') or 0):+.2%}, "
                f"6h {float(best.get('return_6h') or 0):+.2%}, "
                f"volume {float(best.get('volume_ratio') or 0):.2f}x; "
                f"{quote} available {cash_by_quote.get(quote, 0.0):.8g}."
                f"{listing_note}"
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

        decision = Decision(
            "HOLD",
            None,
            0.0,
            "No Coinbase spot trade clears the current threshold.",
        )
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
                try:
                    self._execute_sell(decision, before)
                    status = "LIVE_EXECUTED"
                except Exception as exc:
                    status = "EXECUTION_ERROR"
                    decision = Decision(
                        decision.action,
                        decision.symbol,
                        decision.fraction,
                        f"{decision.rationale} Execution failed: {exc}",
                    )

        if status == "HELD" and best is not None:
            product_id = str(best.get("product") or "").upper()
            score = float(best.get("decision_score") or best.get("score") or 0.0)
            spread_bps = float(best.get("spread_bps") or 0.0)
            volume_ratio = float(best.get("volume_ratio") or 0.0)
            _, quote = self._split_product(product_id)
            available_quote = float(
                (before.get("cash_by_quote") or {}).get(quote, 0.0)
            )

            if (
                score >= 0.18
                and spread_bps <= 50.0
                and volume_ratio >= 0.40
                and available_quote > 0.0
            ):
                fraction = min(0.85, max(0.20, 0.20 + abs(score) * 0.65))
                listing_event = best.get("listing_event")
                listing_note = ""
                if isinstance(listing_event, dict):
                    listing_note = (
                        f" Public listing signal {listing_event.get('event')} "
                        f"/ {listing_event.get('stage')}."
                    )
                decision = Decision(
                    "BUY",
                    product_id,
                    fraction,
                    (
                        f"Best Coinbase-wide executable spot setup at {score:+.3f}; "
                        f"spread {spread_bps:.1f} bps, volume {volume_ratio:.2f}x."
                        f"{listing_note}"
                    ),
                )
                try:
                    self._execute_buy(decision, before)
                    status = "LIVE_EXECUTED"
                except Exception as exc:
                    status = "EXECUTION_ERROR"
                    decision = Decision(
                        decision.action,
                        decision.symbol,
                        decision.fraction,
                        f"{decision.rationale} Execution failed: {exc}",
                    )

        # Every cycle is journaled, including exchange/API execution failures.
        self.store.add_decision(decision, status)
        return self.sync()

    def _execute_buy(self, decision: Decision, snapshot: dict[str, Any]) -> None:
        product_id = str(decision.symbol)
        _, quote = self._split_product(product_id)
        available = Decimal(str((snapshot.get("cash_by_quote") or {}).get(quote, 0.0)))
        spend = available * Decimal(str(decision.fraction))
        if spend <= 0:
            raise RuntimeError(f"Insufficient {quote} for Coinbase Advanced buy.")

        preview = self.rail.preview_market_buy(product_id, spend)
        order = self.rail.market_buy(
            product_id,
            spend,
            client_order_id=f"aiscend-{uuid.uuid4()}",
        )
        self.store.add_ledger(
            "ADVANCED_BUY",
            product_id,
            0.0,
            0.0,
            -float(spend),
            f"{decision.rationale} | preview={str(preview)[:220]} | order={str(order)[:220]}",
        )

    def _execute_sell(self, decision: Decision, snapshot: dict[str, Any]) -> None:
        product_id = str(decision.symbol)
        position = (snapshot.get("positions") or {}).get(product_id) or {}
        qty = Decimal(str(position.get("qty") or 0.0)) * Decimal(str(decision.fraction))
        if qty <= 0:
            raise RuntimeError(f"No {product_id} position available to sell.")

        order = self.rail.market_sell(
            product_id,
            qty,
            client_order_id=f"aiscend-{uuid.uuid4()}",
        )
        self.store.add_ledger(
            "ADVANCED_SELL",
            product_id,
            float(qty),
            0.0,
            0.0,
            f"{decision.rationale} | order={str(order)[:220]}",
        )
