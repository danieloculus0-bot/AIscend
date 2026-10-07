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
from .listing_sentinel import CoinbaseListingSentinel
from .research import MarketResearch
from .sentiment import HumanSignalResearch, HumanSignals
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
        human_researcher: HumanSignalResearch | None = None,
        bean: BeanMemory | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        self.store = store
        self.rail = rail or AdvancedTradeSpot()
        self.asset_universe = asset_universe or CoinbaseAssetUniverse()
        universe_fetch = getattr(self.asset_universe, "_get_json", None)
        self.listing_sentinel = listing_sentinel or CoinbaseListingSentinel(
            self.store,
            fetch_json=universe_fetch,
        )
        self.human_researcher = human_researcher or HumanSignalResearch(
            timeout=7.0,
            cache_seconds=120.0,
        )
        self.bean = bean or BeanMemory(self.store.conn)
        self.audit = audit or AuditTrail()
        self._snapshot: dict[str, Any] | None = None

    @staticmethod
    def _split_product(product_id: str) -> tuple[str, str]:
        base, _, quote = product_id.upper().rpartition("-")
        return base, quote

    def _funding_route(
        self,
        product_id: str,
        product_ids: tuple[str, ...],
        cash_by_quote: dict[str, float],
    ) -> dict[str, Any] | None:
        base, target_quote = self._split_product(product_id)
        direct = float(cash_by_quote.get(target_quote, 0.0))
        if direct > 0.0:
            return {
                "mode": "DIRECT",
                "signal_product": product_id,
                "execution_product": product_id,
                "quote": target_quote,
                "funding_quote": target_quote,
                "available": direct,
            }

        funded_quotes = sorted(
            (
                (str(quote).upper(), float(amount))
                for quote, amount in cash_by_quote.items()
                if float(amount) > 0.0
            ),
            key=lambda row: row[1],
            reverse=True,
        )

        # Prefer the same base asset quoted in currency we already hold.
        for funded_quote, amount in funded_quotes:
            alternate = f"{base}-{funded_quote}"
            if alternate in product_ids:
                return {
                    "mode": "ALT_PAIR",
                    "signal_product": product_id,
                    "execution_product": alternate,
                    "quote": funded_quote,
                    "funding_quote": funded_quote,
                    "available": amount,
                }

        # Coinbase commonly exposes USD and USDC as a directly tradable pair.
        # If the signal only exists on one dollar quote, fund it automatically
        # from the other rather than throwing away an otherwise executable setup.
        if target_quote in DOLLAR_ASSETS:
            for funded_quote, amount in funded_quotes:
                if funded_quote not in DOLLAR_ASSETS or funded_quote == target_quote:
                    continue

                sell_pair = f"{funded_quote}-{target_quote}"
                if sell_pair in product_ids:
                    return {
                        "mode": "CONVERT",
                        "signal_product": product_id,
                        "execution_product": product_id,
                        "quote": target_quote,
                        "funding_quote": funded_quote,
                        "available": amount,
                        "conversion_product": sell_pair,
                        "conversion_side": "SELL",
                    }

                buy_pair = f"{target_quote}-{funded_quote}"
                if buy_pair in product_ids:
                    return {
                        "mode": "CONVERT",
                        "signal_product": product_id,
                        "execution_product": product_id,
                        "quote": target_quote,
                        "funding_quote": funded_quote,
                        "available": amount,
                        "conversion_product": buy_pair,
                        "conversion_side": "BUY",
                    }

        return None

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

    def _human_overlay(
        self,
        item: dict[str, Any],
        base_score: float,
        human: HumanSignals | None,
    ) -> tuple[float, dict[str, Any] | None, float, float]:
        if human is None or not human.available:
            return base_score, None, 0.0, 0.0

        def squash(value: float, scale: float) -> float:
            return math.tanh(value / scale) if scale else 0.0

        momentum_5m = squash(float(item.get("return_5m") or 0.0), 0.005)
        momentum_1h = squash(float(item.get("return_1h") or 0.0), 0.015)
        volume_heat = max(
            0.0,
            squash(float(item.get("volume_ratio") or 1.0) - 1.0, 0.8),
        )
        technical_heat = 50.0 + 50.0 * max(
            -1.0,
            min(
                1.0,
                0.46 * momentum_5m
                + 0.34 * momentum_1h
                + 0.20 * volume_heat,
            ),
        )
        fomo = max(
            0.0,
            min(
                100.0,
                0.68 * human.raw_fomo_index + 0.32 * technical_heat,
            ),
        )
        crowd_regime = HumanSignalResearch._crowd_regime(fomo)
        crowd_edge = MarketResearch._crowd_edge(
            fomo=fomo,
            momentum_5m=momentum_5m,
            momentum_1h=momentum_1h,
            volume_heat=volume_heat,
        )
        fg_delta = max(
            -1.0,
            min(1.0, human.fear_greed_change_1d / 20.0),
        )
        news_maturity = 0.35 + 0.65 * human.news_coverage
        social_maturity = 0.25 + 0.75 * human.social_confidence
        politics_maturity = 0.35 + 0.65 * human.politics_coverage

        human_score = (
            0.28 * human.news_sentiment * news_maturity
            + 0.30 * human.social_sentiment * social_maturity
            + 0.12 * human.politics_sentiment * politics_maturity
            + 0.22 * crowd_edge
            + 0.08 * fg_delta
        )
        human_score = max(-1.0, min(1.0, human_score))

        event_intensity = max(human.politics_risk, human.weirdness)
        human_weight = (
            0.26 + 0.12 * event_intensity
        ) * (
            0.65 + 0.35 * human.human_signal_quality
        )
        human_weight = max(0.0, min(0.45, human_weight))

        fused = (
            (1.0 - human_weight) * base_score
            + human_weight * human_score
        )
        fused = max(-1.0, min(1.0, fused))

        context = human.as_dict()
        context["fomo_index"] = fomo
        context["crowd_regime"] = crowd_regime
        context["advanced_human_score"] = human_score
        context["advanced_human_weight"] = human_weight
        context["advanced_crowd_edge"] = crowd_edge
        return fused, context, human_score, human_weight

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
        human_error = None
        try:
            human = self.human_researcher.collect()
        except Exception as exc:
            human = None
            human_error = str(exc)
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
        # USD/USDC can also fund the other dollar quote through a directly
        # tradable conversion pair, even if one is not otherwise a quote in the
        # current product set.
        for dollar in DOLLAR_ASSETS:
            if raw_balances.get(dollar, 0.0) > 0.000000001:
                cash_by_quote[dollar] = raw_balances[dollar]

        # Preserve the game's dollar-denominated bankroll accounting while
        # allowing execution to use any funded quote currency.
        cash = sum(raw_balances.get(asset, 0.0) for asset in DOLLAR_ASSETS)

        scored_rows = universe.get("scored") or universe.get("top") or []
        universe_by_product = {
            str(item.get("product") or "").upper(): item
            for item in scored_rows
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
            for item in scored_rows:
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
            human,
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
                "human": radar.get("human") or (
                    human.as_dict()
                    if human is not None
                    else {
                        "available": False,
                        "errors": [human_error] if human_error else [],
                    }
                ),
                "human_score": radar.get("human_score", 0.0),
                "human_weight": radar.get("human_weight", 0.0),
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
        human: HumanSignals | None,
    ) -> dict[str, Any]:
        tradable = set(product_ids)
        hot_by_product = {
            str(event.get("product") or "").upper(): event
            for event in (listing.get("hot") or [])
            if str(event.get("stage") or "") == "FULL_TRADING"
        }

        rows: list[dict[str, Any]] = []
        now = time.time()
        source_rows = universe.get("scored") or universe.get("top") or []
        for source in source_rows:
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
            pre_human_score = max(
                -1.0,
                min(1.0, raw_score + listing_boost),
            )
            fused_score, human_context, human_score, human_weight = (
                self._human_overlay(
                    item,
                    pre_human_score,
                    human,
                )
            )
            item["pre_human_score"] = pre_human_score
            item["human_score"] = human_score
            item["human_weight"] = human_weight
            item["human_context"] = human_context
            item["decision_score"] = fused_score
            item["funding_route"] = self._funding_route(
                product,
                product_ids,
                cash_by_quote,
            )
            rows.append(item)

        longs = sorted(
            rows,
            key=lambda item: float(item.get("decision_score") or 0.0),
            reverse=True,
        )
        executable_longs = [
            item
            for item in longs
            if item.get("funding_route")
        ]
        best = executable_longs[0] if executable_longs else (longs[0] if longs else None)
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
            min(
                held,
                key=lambda item: float(
                    item.get("decision_score") or item.get("score") or 0.0
                ),
            )
            if held
            else None
        )

        score = float(best.get("decision_score") or best.get("score") or 0.0) if best else 0.0
        confidence = min(0.95, 0.35 + abs(score) * 0.60) if best else 0.0
        prediction = "BULLISH" if score >= 0.16 else "BEARISH" if score <= -0.16 else "NEUTRAL"
        if best:
            product = str(best.get("product") or "")
            route = best.get("funding_route") or {}
            execution_product = str(route.get("execution_product") or product)
            quote = str(route.get("quote") or self._split_product(execution_product)[1])
            funded = bool(route)
            listing_event = best.get("listing_event")
            listing_note = ""
            if isinstance(listing_event, dict):
                listing_note = (
                    f" Public Coinbase listing signal {listing_event.get('event')} "
                    f"at stage {listing_event.get('stage')}."
                )
            human_context = best.get("human_context") or {}
            human_note = ""
            if human_context:
                human_note = (
                    f" Human Weather {human_context.get('crowd_regime', 'UNKNOWN')} "
                    f"FOMO {float(human_context.get('fomo_index') or 0):.0f}/100, "
                    f"human {float(best.get('human_score') or 0):+.3f} at "
                    f"{float(best.get('human_weight') or 0) * 100:.0f}% weight."
                )
            route_note = ""
            if route.get("mode") == "ALT_PAIR":
                route_note = (
                    f" Signal from {product}; execution rerouted to {execution_product} "
                    f"using funded {quote}."
                )
            elif route.get("mode") == "CONVERT":
                route_note = (
                    f" Will convert {route.get('funding_quote')} to {quote} through "
                    f"{route.get('conversion_product')} before execution."
                )
            thesis = (
                f"Best {'funded ' if funded else ''}tradable spot candidate {product}: "
                f"decision score {score:+.3f}, raw {float(best.get('score') or 0):+.3f}, "
                f"1h {float(best.get('return_1h') or 0):+.2%}, "
                f"6h {float(best.get('return_6h') or 0):+.2%}, "
                f"volume {float(best.get('volume_ratio') or 0):.2f}x; "
                f"{quote} funding available {float(route.get('available') or 0.0):.8g}."
                f"{route_note}"
                f"{listing_note}"
                f"{human_note}"
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
            "human": (
                best.get("human_context")
                if best is not None
                else (human.as_dict() if human is not None else {})
            ),
            "human_score": (
                float(best.get("human_score") or 0.0)
                if best is not None
                else 0.0
            ),
            "human_weight": (
                float(best.get("human_weight") or 0.0)
                if best is not None
                else 0.0
            ),
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
            held_score = float(
                weakest.get("decision_score")
                or weakest.get("score")
                or 0.0
            )
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
            signal_product = str(best.get("product") or "").upper()
            route = best.get("funding_route") or {}
            product_id = str(route.get("execution_product") or signal_product).upper()
            score = float(best.get("decision_score") or best.get("score") or 0.0)
            spread_bps = float(best.get("spread_bps") or 0.0)
            volume_ratio = float(best.get("volume_ratio") or 0.0)
            available_quote = float(route.get("available") or 0.0)

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
                human_context = best.get("human_context") or {}
                human_note = ""
                if human_context:
                    human_note = (
                        f" Human Weather {human_context.get('crowd_regime', 'UNKNOWN')} "
                        f"FOMO {float(human_context.get('fomo_index') or 0):.0f}/100; "
                        f"human {float(best.get('human_score') or 0):+.3f} "
                        f"at {float(best.get('human_weight') or 0) * 100:.0f}% weight."
                    )
                route_note = ""
                if route.get("mode") == "ALT_PAIR":
                    route_note = (
                        f" Signal source {signal_product}; executing {product_id} "
                        f"against funded {route.get('quote')}."
                    )
                elif route.get("mode") == "CONVERT":
                    route_note = (
                        f" Auto-funding {route.get('quote')} from {route.get('funding_quote')} "
                        f"through {route.get('conversion_product')}."
                    )
                decision = Decision(
                    "BUY",
                    product_id,
                    fraction,
                    (
                        f"Best Coinbase-wide executable spot setup at {score:+.3f}; "
                        f"spread {spread_bps:.1f} bps, volume {volume_ratio:.2f}x."
                        f"{route_note}"
                        f"{listing_note}"
                        f"{human_note}"
                    ),
                )
                try:
                    self._execute_buy(decision, before, funding_route=route)
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

    @staticmethod
    def _submitted_order_id(order: dict[str, Any]) -> str:
        success_response = order.get("success_response") or {}
        return str(
            success_response.get("order_id")
            or order.get("order_id")
            or ""
        ).strip()

    def _confirm_order_fill(
        self,
        order: dict[str, Any],
        product_id: str,
    ) -> dict[str, Any] | None:
        fetch_order = getattr(self.rail, "order", None)
        if not callable(fetch_order):
            return None

        order_id = self._submitted_order_id(order)
        if not order_id:
            raise RuntimeError(
                f"Coinbase accepted {product_id} without an order id."
            )

        last: dict[str, Any] = {}
        for attempt in range(8):
            payload = fetch_order(order_id)
            details = payload.get("order") or payload
            if isinstance(details, dict):
                last = details
                status = str(details.get("status") or "").upper()
                reject_reason = str(
                    details.get("reject_message")
                    or details.get("reject_reason")
                    or details.get("cancel_message")
                    or ""
                ).strip()
                filled_size = Decimal(str(details.get("filled_size") or "0"))
                filled_value = Decimal(str(details.get("filled_value") or "0"))
                settled = bool(details.get("settled", False))

                if filled_size > 0 or filled_value > 0 or settled or status == "FILLED":
                    return details

                if status in {"FAILED", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED"}:
                    raise RuntimeError(
                        f"Coinbase order {order_id} ended {status}"
                        + (f": {reject_reason}" if reject_reason else "")
                    )
            if attempt < 7:
                time.sleep(0.5)

        raise RuntimeError(
            f"Coinbase order {order_id} was submitted but no fill was confirmed. "
            f"Last status: {last.get('status') or 'unknown'}."
        )

    def _execute_buy(
        self,
        decision: Decision,
        snapshot: dict[str, Any],
        funding_route: dict[str, Any] | None = None,
    ) -> None:
        product_id = str(decision.symbol)
        _, quote = self._split_product(product_id)
        route = funding_route or {}
        mode = str(route.get("mode") or "DIRECT").upper()

        if mode == "CONVERT":
            funding_quote = str(route.get("funding_quote") or "").upper()
            available_funding = Decimal(
                str((snapshot.get("cash_by_quote") or {}).get(funding_quote, 0.0))
            )
            convert_amount = available_funding * Decimal(str(decision.fraction))
            if convert_amount <= 0:
                raise RuntimeError(
                    f"Insufficient {funding_quote} to fund {quote} conversion."
                )
            self._execute_quote_conversion(
                route,
                convert_amount,
                decision.rationale,
            )

            target_available = Decimal("0")
            for attempt in range(6):
                refreshed = self.rail.status()
                target_available = next(
                    (
                        Decimal(str(item.available))
                        for item in refreshed.balances
                        if item.currency.upper() == quote
                    ),
                    Decimal("0"),
                )
                if target_available > 0:
                    break
                if attempt < 5:
                    time.sleep(0.5)
            spend = target_available * Decimal("0.995")
        else:
            available = Decimal(
                str((snapshot.get("cash_by_quote") or {}).get(quote, 0.0))
            )
            spend = available * Decimal(str(decision.fraction))

        if spend <= 0:
            raise RuntimeError(f"Insufficient {quote} for Coinbase Advanced buy.")

        preview = self.rail.preview_market_buy(product_id, spend)
        client_order_id = f"aiscend-{uuid.uuid4()}"
        order = self.rail.market_buy(
            product_id,
            spend,
            client_order_id=client_order_id,
        )
        confirmed_order = self._confirm_order_fill(order, product_id)
        self.store.add_ledger(
            "ADVANCED_BUY",
            product_id,
            0.0,
            0.0,
            -float(spend),
            f"{decision.rationale} | preview={str(preview)[:220]} | order={str(order)[:220]}",
        )

        try:
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
                raw_provider_response={
                    "preview": preview,
                    "order": order,
                    "confirmed_order": confirmed_order,
                },
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

    def _execute_quote_conversion(
        self,
        route: dict[str, Any],
        amount: Decimal,
        rationale: str,
    ) -> None:
        conversion_product = str(route.get("conversion_product") or "").upper()
        conversion_side = str(route.get("conversion_side") or "").upper()
        if not conversion_product or conversion_side not in {"BUY", "SELL"}:
            raise RuntimeError("Invalid quote-conversion route.")

        client_order_id = f"aiscend-quote-{uuid.uuid4()}"
        if conversion_side == "SELL":
            order = self.rail.market_sell(
                conversion_product,
                amount,
                client_order_id=client_order_id,
            )
        else:
            order = self.rail.market_buy(
                conversion_product,
                amount,
                client_order_id=client_order_id,
            )
        confirmed_order = self._confirm_order_fill(order, conversion_product)

        self.store.add_ledger(
            "ADVANCED_QUOTE_CONVERSION",
            conversion_product,
            float(amount) if conversion_side == "SELL" else 0.0,
            0.0,
            -float(amount) if conversion_side == "BUY" else 0.0,
            (
                f"Auto-funded quote currency before buy. {rationale} | "
                f"order={str(order)[:220]}"
            ),
        )

        try:
            base, quote = self._split_product(conversion_product)
            self.audit.record_trade(
                venue="coinbase",
                rail="coinbase-advanced",
                network="coinbase",
                side=conversion_side,
                product_id=conversion_product,
                base_asset=base,
                quote_asset=quote,
                base_quantity=amount if conversion_side == "SELL" else None,
                quote_quantity=amount if conversion_side == "BUY" else None,
                order_id=self.audit.provider_id(order, "order_id", "orderId"),
                client_order_id=client_order_id,
                status="provider_response",
                rationale=f"Automatic quote funding. {rationale}",
                source="coinbase_advanced_quote_conversion",
                raw_provider_response={
                    "order": order,
                    "confirmed_order": confirmed_order,
                },
            )
        except Exception as exc:
            self.store.add_ledger(
                "AUDIT_ERROR",
                conversion_product,
                0.0,
                0.0,
                0.0,
                f"Quote conversion executed but audit write failed: {exc}",
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
        confirmed_order = self._confirm_order_fill(order, product_id)
        self.store.add_ledger(
            "ADVANCED_SELL",
            product_id,
            float(qty),
            0.0,
            0.0,
            f"{decision.rationale} | order={str(order)[:220]}",
        )

        try:
            base, quote = self._split_product(product_id)
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
                raw_provider_response={
                    "order": order,
                    "confirmed_order": confirmed_order,
                },
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

