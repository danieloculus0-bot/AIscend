from __future__ import annotations

import json
import math
import time
import uuid
from decimal import Decimal
from typing import Any

from .asset_universe import CoinbaseAssetUniverse
from .audit import AuditTrail
from .bean import BeanMemory
from .bean_policy import BeanPolicyGate
from .bean_growth_rotation import evaluate_candidates
from .core import Decision, StateStore
from .game import score_game
from .listing_sentinel import CoinbaseListingSentinel
from .research import MarketResearch
from .sentiment import HumanSignalResearch, HumanSignals
from .wallets.advanced_trade import AdvancedTradeSpot, require_accepted_order


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
        policy_gate: BeanPolicyGate | None = None,
        growth_gate=None,
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
        self.policy_gate = policy_gate or BeanPolicyGate()
        self.growth_gate = growth_gate
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

        # Holdings reserved by open orders remain part of portfolio value,
        # but must never be treated as spendable buying/selling inventory.
        spendable_balances = {
            item.currency: float(item.available)
            for item in status.balances
            if float(item.available) > 0.000000001
        }
        raw_balances = {
            item.currency: float(item.available + item.hold)
            for item in status.balances
            if float(item.available + item.hold) > 0.000000001
        }
        quote_currencies = {
            self._split_product(product_id)[1]
            for product_id in product_ids
            if self._split_product(product_id)[1]
        }
        cash_by_quote = {
            quote: spendable_balances.get(quote, 0.0)
            for quote in quote_currencies
            if spendable_balances.get(quote, 0.0) > 0.000000001
        }

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
                "available_qty": spendable_balances.get(currency, 0.0),
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
            "balance_on_hold": {item.currency: float(item.hold) for item in status.balances if item.hold > 0},
            "valuation_complete": all(currency in balance_values_usd for currency in raw_balances),
            "unpriced_balances": {currency: qty for currency, qty in raw_balances.items() if currency not in balance_values_usd},
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
            "tradable_products": list(product_ids),
            "listing_sentinel": listing,
            "bean": self.bean.snapshot(),
            "bean_policy": json.loads(self.store.get_meta("bean_policy_latest") or "{}"),
            "bean_growth": json.loads(self.store.get_meta("bean_growth_latest") or "{}"),
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
            min(
                held,
                key=lambda item: float(
                    (item.get("decision_score") if item.get("decision_score") is not None else item.get("score") or 0.0)
                ),
            )
            if held
            else None
        )

        score = float((best.get("decision_score") if best.get("decision_score") is not None else best.get("score") or 0.0)) if best else 0.0
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
            human_context = best.get("human_context") or {}
            human_note = ""
            if human_context:
                human_note = (
                    f" Human Weather {human_context.get('crowd_regime', 'UNKNOWN')} "
                    f"FOMO {float(human_context.get('fomo_index') or 0):.0f}/100, "
                    f"human {float(best.get('human_score') or 0):+.3f} at "
                    f"{float(best.get('human_weight') or 0) * 100:.0f}% weight."
                )
            thesis = (
                f"Best {'funded ' if funded else ''}tradable spot candidate {product}: "
                f"decision score {score:+.3f}, raw {float(best.get('score') or 0):+.3f}, "
                f"1h {float(best.get('return_1h') or 0):+.2%}, "
                f"6h {float(best.get('return_6h') or 0):+.2%}, "
                f"volume {float(best.get('volume_ratio') or 0):.2f}x; "
                f"{quote} available {cash_by_quote.get(quote, 0.0):.8g}."
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

    def _adopt_growth(self, snapshot, radar):
        """Use the learned policy to execute a BUY, SELL or HOLD, when ready."""
        if self.growth_gate is None:
            return None
        selection_snapshot = snapshot
        if not (snapshot.get("asset_universe") or {}).get("scored"):
            # Keep the direct engine's own market radar usable with callers
            # that provide a minimized snapshot in integration tests.
            seeds = [x for x in (radar.get("best_long"),
                                  radar.get("weakest_held")) if x]
            selection_snapshot = dict(snapshot)
            selection_snapshot["asset_universe"] = {"scored": seeds}
        cursor = int(self.store.get_meta("bean_rotation_cursor") or "0")
        plan = evaluate_candidates(self.growth_gate, selection_snapshot,
                                   cursor=cursor)
        self.store.set_meta("bean_rotation_cursor", str(plan["next_cursor"]))
        winner = plan["best_long"]
        # Prefer markets funded by spendable cash or a held asset that can
        # actually be liquidated into the market's quote currency. Otherwise
        # a nominally better but unreachable USD market can strand USDC.
        accessible_quotes = {
            quote for quote, value in (snapshot.get("cash_by_quote") or {}).items()
            if float(value) > 0
        }
        accessible_quotes.update(
            self._split_product(product)[1]
            for product in (snapshot.get("positions") or {})
        )
        if winner and self._split_product(winner["product"])[1] not in accessible_quotes:
            alternatives = sorted(
                (item for item in plan["evaluated"]
                 if item["target_exposure"]
                 and self._split_product(item["product"])[1] in accessible_quotes),
                key=lambda item: item["edge_bps"], reverse=True)
            winner = alternatives[0] if alternatives else None
        evaluated = {x["product"]: x for x in plan["evaluated"]}
        self.store.set_meta("bean_growth_latest", json.dumps({
            "trade_effect": "ADOPTED_PORTFOLIO_STRATEGY",
            "winner": winner, "assessed_count": plan["assessed_count"],
            "eligible_count": plan["eligible_count"],
            "universe_count": plan["universe_count"],
            "next_cursor": plan["next_cursor"],
            "ranked": sorted(plan["evaluated"],
                             key=lambda x: x["edge_bps"], reverse=True),
        }))

        for held_product, holding in (snapshot.get("positions") or {}).items():
            available = float(holding.get("available_qty",
                                          holding.get("qty", 0)) or 0)
            if available <= 0:
                continue
            current = evaluated.get(held_product)
            if current is None:
                continue
            target = winner["product"] if winner else None
            old_edge = current["edge_bps"] if current["target_exposure"] else 0
            new_edge = winner["edge_bps"] if winner else 0
            # Rank on modeled net rewards rather than an extra fixed 18-bps
            # veto. Exit learned cash/negative-edge holds to free capital.
            rotate = bool(winner and target != held_product
                          and new_edge > old_edge)
            if (not current["target_exposure"]) or rotate:
                decision = Decision("SELL", held_product, 1.0,
                    "BEAN learned rotation " + held_product + " to "
                    + (target or "cash") + "; learned net edge "
                    + f"{new_edge-old_edge:+.2f} bps.")
                try:
                    self._execute_sell(decision, snapshot)
                    status = "LIVE_EXECUTED"
                except Exception as exc:
                    status = "EXECUTION_ERROR"
                    decision = Decision(decision.action, decision.symbol,
                                        decision.fraction,
                                        f"{decision.rationale} Execution failed: {exc}")
                self.store.add_decision(decision, status)
                return self.sync()

        if winner is None:
            if plan["evaluated"]:
                decision = Decision("HOLD", None, 0.0,
                                    "BEAN learned policy favors cash; no positive long strategy.")
                self.store.add_decision(decision, "HELD")
                return self.sync()
            return None
        best = {"product": winner["product"],
                "decision_score": winner["edge_bps"] / 100.0,
                "spread_bps": winner["spread_bps"],
                "volume_ratio": winner["volume_ratio"]}
        product = winner["product"]
        growth = {"trade_effect": "ADOPTED_LONG_CASH",
                  "learned_strategy": winner["action"],
                  "target_exposure": winner["target_exposure"]}
        action = "HOLD"
        fraction = 0.0
        symbol = None
        rationale = ("BEAN learned " + str(growth["learned_strategy"])
                     + " selected " + ("long" if growth["target_exposure"] else "cash") + ".")
        if growth["target_exposure"]:
            score = float(best.get("decision_score") if best.get("decision_score") is not None
                          else best.get("score") or 0.0)
            spread = float(best.get("spread_bps") or 0.0)
            volume = float(best.get("volume_ratio") or 0.0)
            _, quote = self._split_product(product)
            cash = float((snapshot.get("cash_by_quote") or {}).get(quote, 0.0))
            # A learned long target is a position, not permission to
            # repeatedly buy the same already-held asset every minute.
            if cash > 0.0 and product not in snapshot.get("positions", {}):
                action, symbol = "BUY", product
                fraction = 1.0
        if action == "HOLD" and growth["target_exposure"]:
            _, quote = self._split_product(product)
            available_cash = float((snapshot.get("cash_by_quote") or {}).get(quote, 0))
            if available_cash <= 0:
                rationale += f" No spendable {quote} for {product}."
            elif product in snapshot.get("positions", {}):
                rationale += " Position already held."
        decision = Decision(action, symbol, fraction, rationale)
        status = "HELD"
        if action == "BUY":
            try:
                self._execute_buy(decision, snapshot)
                status = "LIVE_EXECUTED"
            except Exception as exc:
                status = "EXECUTION_ERROR"
                decision = Decision(decision.action, decision.symbol,
                                    decision.fraction,
                                    f"{decision.rationale} Execution failed: {exc}")
        self.store.add_decision(decision, status)
        return self.sync()

    def step(self) -> dict[str, Any]:
        before = self.sync()
        try:
            policy = self.policy_gate.assess()
        except Exception as exc:
            policy = {"status": "DATA_UNAVAILABLE", "trade_effect": "NONE",
                      "reason": f"Policy evaluation failed: {type(exc).__name__}"}
        self.store.set_meta("bean_policy_latest", json.dumps(policy))
        radar = before.get("market_radar") or {}
        best = radar.get("best_long")
        weakest = radar.get("weakest_held")
        adopted = self._adopt_growth(before, radar)
        if adopted is not None:
            return adopted

        decision = Decision(
            "HOLD",
            None,
            0.0,
            "No positive opportunity currently executable with available funds.",
        )
        status = "HELD"

        if weakest is not None:
            held_product = str(weakest.get("product") or "").upper()
            held_score = float(
                weakest.get("decision_score") if weakest.get("decision_score") is not None else (weakest.get("score") or 0.0)
            )
            best_score = float(
                (best.get("decision_score") if best and best.get("decision_score") is not None
                 else best.get("score") or 0.0) if best else 0.0
            )
            improvement = bool(
                best and best.get("product") != held_product
                and best_score > max(0.0, held_score)
            )
            if (held_score < 0.0 or improvement) and held_product in before["positions"]:
                fraction = 1.0
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
            score = float((best.get("decision_score") if best.get("decision_score") is not None else best.get("score") or 0.0))
            spread_bps = float(best.get("spread_bps") or 0.0)
            volume_ratio = float(best.get("volume_ratio") or 0.0)
            _, quote = self._split_product(product_id)
            available_quote = float(
                (before.get("cash_by_quote") or {}).get(quote, 0.0)
            )

            if (
                score > 0.0
                and available_quote > 0.0
                and product_id not in before["positions"]
            ):
                fraction = 1.0
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
                decision = Decision(
                    "BUY",
                    product_id,
                    fraction,
                    (
                        f"Best Coinbase-wide executable spot setup at {score:+.3f}; "
                        f"spread {spread_bps:.1f} bps, volume {volume_ratio:.2f}x."
                        f"{listing_note}"
                        f"{human_note}"
                    ),
                )
                # Strategy School is advisory. A historical single-asset
                # exam must not veto a live opportunity across the universe.
                if decision.action == "BUY" and self.growth_gate is not None:
                    growth = self.growth_gate.assess(decision.symbol)
                    self.store.set_meta("bean_growth_latest", json.dumps(growth))
                    if (growth.get("trade_effect") == "BUY_VETO_ONLY"
                            and not growth.get("allow_buy", True)):
                        decision = Decision(
                            "HOLD", None, 0.0,
                            "BEAN adaptive growth vetoed BUY: learned "
                            + str(growth.get("learned_strategy"))
                            + " selected mock cash exposure.")
                if decision.action == "BUY":
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
        client_order_id = f"aiscend-{uuid.uuid4()}"
        order = self.rail.market_buy(
            product_id,
            spend,
            client_order_id=client_order_id,
        )
        require_accepted_order(order)
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
        qty = Decimal(str(position.get("available_qty", position.get("qty", 0.0)) or 0.0)) * Decimal(str(decision.fraction))
        if qty <= 0:
            raise RuntimeError(f"No {product_id} position available to sell.")

        client_order_id = f"aiscend-{uuid.uuid4()}"
        order = self.rail.market_sell(
            product_id,
            qty,
            client_order_id=client_order_id,
        )
        require_accepted_order(order)
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

