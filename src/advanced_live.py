from __future__ import annotations

import json
import math
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any

from .asset_universe import CoinbaseAssetUniverse
from .audit import AuditTrail
from .bean import BeanMemory
from .bean_policy import BeanPolicyGate
from .bean_growth_rotation import evaluate_candidates
from .bean_capital_evidence import evaluate_account, capital_snapshot
from .bean_rotation_economics import (score_route, bridge_order,
                                      maturity_minutes, trade_cost_bps)
from .position_cost_basis import rebuild_position_basis
from .primary_strategy import (
    select_plan, proposals, exit_reason, incumbent_hold_net_bps,
    ROTATION_MARGIN_BPS,
)
from .coinbase_order_evidence import accepted_order_id, verified_order
from .core import Decision, StateStore
from .game import score_game
from .listing_sentinel import CoinbaseListingSentinel
from .research import MarketResearch
from .sentiment import HumanSignalResearch, HumanSignals
from .wallets.advanced_trade import (AdvancedTradeSpot, require_accepted_order,
                                     CoinbaseExplicitRejection)


DOLLAR_ASSETS = {"USD", "USDC"}

def _position_meta_key(prefix: str, product: str, scope: str | None) -> str:
    """Partition lifecycle state by authenticated Coinbase key."""
    if (not isinstance(scope, str) or len(scope) != 64
            or any(c not in "0123456789abcdef" for c in scope)):
        scope = "unverified"
    return f"{prefix}{scope}:{product}"



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

    def snapshot(self) -> dict[str, Any]:
        """Provide the same snapshot interface as Base and synthetic engines.

        The desktop refresh must not make another network request merely to
        render the state already returned by a successful sync/step.
        """
        if self._snapshot is None:
            return self.sync()
        return self._snapshot

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
        # status() must authenticate first. Never trust a key fingerprint as
        # proof the key has permissions until the venue status call succeeds.
        scope_reader = getattr(self.rail, "account_scope_id", None)
        try:
            authenticated_scope = scope_reader() if callable(scope_reader) else None
        except Exception:
            authenticated_scope = None
        if (not isinstance(authenticated_scope, str)
                or len(authenticated_scope) != 64
                or any(c not in "0123456789abcdef" for c in authenticated_scope)):
            authenticated_scope = None
        listing = self.listing_sentinel.poll()
        priority_products = [
            str(event.get("product") or "").upper()
            for event in (
                list(listing.get("new_products") or [])
                + list(listing.get("changes") or [])
            )
            if event.get("product")
        ]
        # Account holdings must be sampled every scan; otherwise the
        # rotational universe can starve exit/maturity decisions of evidence.
        for balance in status.balances:
            qty = float(balance.available + balance.hold)
            currency = str(balance.currency).upper()
            if qty > 0 and currency not in DOLLAR_ASSETS:
                held_product = self._product_for_currency(
                    currency, tuple(status.product_ids), preferred_quote="USDC")
                if held_product:
                    priority_products.append(held_product)
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

        positions: dict[str, dict[str, float | str | None]] = {}
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
                "avg_cost": None,
                "mark_price": usd_rate,
                "cost_basis_status": "unverified_without_fills",
                "base": currency,
            }
            prices[product_id] = usd_rate

        net = sum(balance_values_usd.values())
        market_value = max(0.0, net - cash)
        # Never reuse a previous Coinbase key's baseline after credentials
        # change. The legacy unscoped value is not migrated automatically.
        baseline_key = ("advanced_starting_value:" + authenticated_scope
                        if authenticated_scope else "advanced_starting_value")
        start_text = self.store.get_meta(baseline_key)
        if start_text is None and net > 0.01:
            start = net
            self.store.set_meta(baseline_key, f"{start:.10f}")
        elif start_text is not None:
            start = float(start_text)
        else:
            start = 0.0

        if time.time() - float(self.store.get_meta("bean_advanced_last_observation") or "0") >= 55.0:
            try:
                self.bean.record_universe(universe)
                bean_prices = {}
                for item in scored_rows:
                    base = str(item.get("base") or "")
                    price = float(item.get("price") or 0.0)
                    if base and price > 0:
                        bean_prices[base] = price
                self.bean.resolve_due(bean_prices)
                self.store.set_meta("bean_advanced_last_observation", str(time.time()))
            except Exception as exc:
                # Advisory memory must never make the trading engine crash.
                self.store.set_meta("bean_sync_warning", str(exc)[:180])

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
            "authenticated_scope": authenticated_scope,
            "positions": positions,
            "prices": prices,
            "returns": {
                product: float(universe_by_product.get(product, {}).get("return_1h") or 0.0)
                for product in positions
            },
            "market_value": market_value,
            "net_liquidation": net,
            "pnl": (net - start) if start else 0.0,
            "pnl_is_verified_trading_profit": False,
            "pnl_semantics": "unadjusted_equity_change_since_first_observed_balance",
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
            "bean_account": json.loads(self.store.get_meta("bean_account_latest") or "{}"),
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

    def _taker_cost_bps(self) -> tuple[float, str]:
        """Use authenticated Coinbase fee tier; cache its source for 15 min."""
        now = time.time()
        # Exchange fee rates belong to the authenticated key/portfolio.
        # Never reuse a different Coinbase key's cheaper historical fee tier.
        scope_reader = getattr(getattr(self, "rail", None), "account_scope_id", None)
        try:
            scope = scope_reader() if callable(scope_reader) else None
        except Exception:
            scope = None
        usable = (isinstance(scope, str) and len(scope) == 64
                  and all(c in "0123456789abcdef" for c in scope))
        if not usable:
            return 60.0, "unverified_account_fee_fallback"
        meta_key = "bean_fee_tier:" + scope
        try:
            saved = json.loads(self.store.get_meta(meta_key) or "{}")
        except (ValueError, TypeError):
            saved = {}
        if now - float(saved.get("checked_at") or 0.0) < 900:
            return float(saved.get("bps", 60.0)), str(saved.get("source", "estimate"))
        rate = None
        try:
            getter = getattr(getattr(self, "rail", None), "taker_fee_bps", None)
            rate = getter() if callable(getter) else None
        except Exception:
            pass
        bps = float(rate) if rate is not None else 60.0
        source = "coinbase_account_fee_tier" if rate is not None else "conservative_estimate"
        self.store.set_meta(meta_key, json.dumps({
            "bps": bps, "source": source, "checked_at": now}))
        return bps, source

    def _execute_quote_bridge(self, source_quote, target_quote, snapshot, rationale):
        bridge = bridge_order(source_quote, target_quote,
                              snapshot.get("tradable_products"))
        if bridge is None:
            raise RuntimeError("No supported Coinbase USD/USDC conversion pair")
        product = bridge["product"]
        cash = float((snapshot.get("cash_by_quote") or {}).get(source_quote) or 0.0)
        if cash <= 0:
            raise RuntimeError(f"No spendable {source_quote} for quote conversion")
        if bridge["side"] == "SELL":
            # USDC-USD is a real spot SELL; the proceeds become USD.
            synthetic = dict(snapshot)
            synthetic["positions"] = dict(snapshot.get("positions") or {})
            synthetic["positions"][product] = {"qty": cash, "available_qty": cash}
            decision = Decision("SELL", product, 1.0, rationale)
            self._execute_sell(decision, synthetic)
        else:
            decision = Decision("BUY", product, 1.0, rationale)
            self._execute_buy(decision, snapshot)
        return decision

    def _adopt_growth(self, snapshot, radar):
        """Compare fee-inclusive routes, then execute at most one leg per cycle.

        BEAN's Q rewards are simulated five-minute observations. A predicted
        growth window is a testable hypothesis, not a calibrated profit promise.
        """
        if self.growth_gate is None:
            return None
        selection_snapshot = snapshot
        if not (snapshot.get("asset_universe") or {}).get("scored"):
            seeds = [x for x in (radar.get("best_long"),
                                  radar.get("weakest_held")) if x]
            selection_snapshot = dict(snapshot)
            selection_snapshot["asset_universe"] = {"scored": seeds}
        cursor = int(self.store.get_meta("bean_rotation_cursor") or "0")
        plan = evaluate_candidates(self.growth_gate, selection_snapshot, cursor=cursor)
        self.store.set_meta("bean_rotation_cursor", str(plan["next_cursor"]))

        fee_bps, fee_source = self._taker_cost_bps()
        cash = snapshot.get("cash_by_quote") or {}
        held = snapshot.get("positions") or {}
        tradable = snapshot.get("tradable_products") or []
        rows = (snapshot.get("asset_universe") or {}).get("scored") or []
        known_spreads = {
            str(row.get("product") or ""): float(row.get("spread_bps") or 0.0)
            for row in rows
        }
        bridge_spread = known_spreads.get("USDC-USD", 10.0)
        source_quotes = {
            quote for quote, amount in cash.items() if float(amount) > 0
        }
        source_quotes.update(self._split_product(p)[1] for p in held)

        ranked = []
        for item in plan["evaluated"]:
            if not item["target_exposure"]:
                ranked.append({**item, "reason_skipped": "learned_cash"})
                continue
            product = item["product"]
            quote = self._split_product(product)[1]
            choices = []
            for source_quote in sorted(source_quotes):
                bridge = source_quote != quote
                if bridge and bridge_order(source_quote, quote, tradable) is None:
                    continue
                for held_product in [None, *held]:
                    selling = held_product is not None and held_product != product
                    owned = held_product == product
                    if held_product and self._split_product(held_product)[1] != source_quote:
                        continue
                    if not selling and not owned and float(cash.get(source_quote, 0.0)) <= 0:
                        continue
                    options = score_route(
                        item, taker_fee_bps=fee_bps, bridge=bridge,
                        bridge_spread_bps=bridge_spread,
                        incumbent_spread_bps=known_spreads.get(held_product, 0.0),
                        selling_incumbent=selling)
                    options["source_quote"] = source_quote
                    options["source_asset"] = held_product if selling else None
                    options["already_held"] = bool(owned)
                    # Choosing the most favorable percentage on $0.88 while
                    # ignoring $20 in existing positions strands the bankroll.
                    available_notional = float(cash.get(source_quote, 0.0))
                    if selling or owned:
                        base = self._split_product(held_product)[0]
                        usd_value = (snapshot.get("balance_values_usd") or {}).get(base)
                        if usd_value is None:
                            qty = float(held[held_product].get("available_qty",
                                        held[held_product].get("qty", 0)) or 0)
                            price = float((snapshot.get("prices") or {}).get(held_product) or 0)
                            usd_value = qty * price if price > 0 else max(1.0, qty)
                        available_notional = float(usd_value)
                    options["available_notional"] = available_notional
                    options["projected_gain_units"] = round(
                        options["net_projected_bps"] * available_notional / 10000, 8)
                    choices.append(options)
            if choices:
                best_route = max(choices, key=lambda x: x["projected_gain_units"])
                # Never submit a loss-expected route merely because the
                # learner lacks a scored policy; exploration can continue
                # through causal paper rewards until an opportunity clears.
                if best_route["net_projected_bps"] <= 0:
                    best_route["reason_skipped"] = "projected_gain_below_route_cost"
                ranked.append(best_route)
            else:
                ranked.append({**item, "reason_skipped": "no_compatible_quote_route"})

        candidates = [
            item for item in ranked if item["target_exposure"]
            and not item.get("reason_skipped")
        ]
        winner = (max(candidates, key=lambda x: x["projected_gain_units"])
                  if candidates else None)
        self.store.set_meta("bean_growth_latest", json.dumps({
            "trade_effect": "ADOPTED_COST_AWARE_PORTFOLIO_STRATEGY",
            "winner": winner, "assessed_count": plan["assessed_count"],
            "eligible_count": plan["eligible_count"],
            "universe_count": plan["universe_count"],
            "next_cursor": plan["next_cursor"], "taker_fee_bps": fee_bps,
            "fee_source": fee_source, "ranked": sorted(
                ranked, key=lambda x: x.get("net_projected_bps", -1e9), reverse=True),
        }))

        now = time.time()
        for held_product, holding in held.items():
            available = float(holding.get("available_qty", holding.get("qty", 0)) or 0)
            if available <= 0:
                continue
            current = next((r for r in plan["evaluated"]
                            if r["product"] == held_product), None)
            key = "bean_maturity_at:" + held_product
            expires = float(self.store.get_meta(key) or "0")
            if not expires:
                # Legacy holdings predate the new strategy clock. Observe them
                # before applying a timed exit, unless BEAN recommends cash.
                expires = now + 60 * (maturity_minutes(current) if current else 60)
                self.store.set_meta(key, str(expires))
            matured = now >= expires
            # Incumbent economics count only its future exit, not a BUY fee
            # already paid. An unknown incumbent is not assumed profitable.
            old_growth = 0.0
            if current and current.get("target_exposure"):
                hypothesis = score_route(current, taker_fee_bps=fee_bps)
                old_growth = hypothesis["estimated_growth_bps"] - trade_cost_bps(
                    current.get("spread_bps", 0), fee_bps)
            to_new = winner and winner["product"] != held_product
            competing = bool(to_new and winner["source_asset"] == held_product
                             and winner["net_projected_bps"] > old_growth)
            learned_exit = bool(current and not current["target_exposure"])
            if matured or competing or learned_exit:
                target = winner["product"] if competing else "cash"
                rationale = (
                    f"BEAN learned {('maturity' if matured else 'rotation')} "
                    f"{held_product} to {target}; horizon deadline {expires:.0f}; "
                    f"incumbent forecast {old_growth:+.2f}bps, "
                    f"replacement net {winner['net_projected_bps'] if winner else 0:+.2f}bps.")
                decision = Decision("SELL", held_product, 1.0, rationale)
                try:
                    self._execute_sell(decision, snapshot)
                    status = (self.store.get_meta("last_coinbase_submission_state")
                              or "ORDER_ACCEPTED_UNVERIFIED")
                    self.store.set_meta(key, "0")
                except Exception as exc:
                    status = ("ORDER_SUBMISSION_UNKNOWN"
                              if self.store.get_meta("pending_coinbase_order")
                              else "EXECUTION_ERROR")
                    decision = Decision("SELL", held_product, 1.0,
                                        rationale + f" Execution failed: {exc}")
                self.store.add_decision(decision, status)
                return self.sync()

        if winner is None:
            reasons = {
                r.get("reason_skipped") for r in ranked if r.get("reason_skipped")
            }
            rationale = ("BEAN reviewed " + str(plan["assessed_count"]) +
                         " assets: no forecast exceeds estimated full route costs. " +
                         "Reasons: " + ", ".join(sorted(reasons)) + ".")
            decision = Decision("HOLD", None, 0.0, rationale)
            self.store.add_decision(decision, "HELD")
            return self.sync()

        product = winner["product"]
        _, target_quote = self._split_product(product)
        if product in held:
            decision = Decision("HOLD", None, 0.0,
                f"BEAN learned {product} held until its maturity; "
                f"projected net {winner['net_projected_bps']:+.2f}bps.")
            self.store.add_decision(decision, "HELD")
            return self.sync()

        # A funded source position must be sold in a distinct confirmed cycle.
        if winner["source_asset"] is not None:
            decision = Decision("HOLD", None, 0.0,
                f"BEAN selected {product}, but release of {winner['source_asset']} "
                "must clear the cost-aware incumbent comparison first.")
            self.store.add_decision(decision, "HELD")
            return self.sync()

        source_quote = winner["source_quote"]
        if source_quote != target_quote:
            rationale = (f"BEAN costed USD/USDC conversion for {product}; "
                         f"projected net {winner['net_projected_bps']:+.2f}bps.")
            try:
                decision = self._execute_quote_bridge(
                    source_quote, target_quote, snapshot, rationale)
                status = (self.store.get_meta("last_coinbase_submission_state")
                              or "ORDER_ACCEPTED_UNVERIFIED")
            except Exception as exc:
                status = ("ORDER_SUBMISSION_UNKNOWN"
                              if self.store.get_meta("pending_coinbase_order")
                              else "EXECUTION_ERROR")
                decision = Decision("HOLD", None, 0.0,
                                    rationale + f" Conversion failed: {exc}")
            self.store.add_decision(decision, status)
            return self.sync()

        rationale = (f"BEAN learned {winner['action']} BUY {product}; "
                     f"provisional maturity {winner['maturity_minutes']} min, "
                     f"modeled gross {winner['estimated_growth_bps']:+.2f}bps, "
                     f"route costs {winner['total_route_cost_bps']:.2f}bps, "
                     f"net {winner['net_projected_bps']:+.2f}bps.")
        decision = Decision("BUY", product, 1.0, rationale)
        try:
            self._execute_buy(decision, snapshot)
            self.store.set_meta("bean_maturity_at:" + product,
                                str(now + 60 * winner["maturity_minutes"]))
            status = (self.store.get_meta("last_coinbase_submission_state")
                              or "ORDER_ACCEPTED_UNVERIFIED")
        except Exception as exc:
            status = ("ORDER_SUBMISSION_UNKNOWN"
                              if self.store.get_meta("pending_coinbase_order")
                              else "EXECUTION_ERROR")
            decision = Decision("BUY", product, 1.0,
                                rationale + f" Execution failed: {exc}")
        self.store.add_decision(decision, status)
        return self.sync()

    def _record_order_intent(self, *, product: str, side: str,
                             client_id: str) -> None:
        """Persist the idempotency key BEFORE a potentially irreversible order.

        A network timeout after Coinbase accepts a trade is ambiguous.
        We never automatically repeat it with a newly generated client ID.
        """
        if not callable(getattr(self.rail, "order_detail", None)):
            # Mock rails are not authenticated Coinbase and have no reconciliation.
            return
        self.store.set_meta("pending_coinbase_order", json.dumps({
            "order_id": None, "client_order_id": client_id,
            "product": product, "side": side,
            "submitted_at": time.time(), "state": "SUBMISSION_OUTCOME_UNKNOWN"}))

    def _check_or_track_submission(self, order: dict[str, Any],
                                   *, product: str, side: str,
                                   client_id: str) -> tuple[str, dict[str, Any]]:
        """A Coinbase submit ACK is NOT evidence of a filled trade."""
        order_id = accepted_order_id(order)
        getter = getattr(self.rail, "order_detail", None)
        if not order_id:
            observation = {"state": "ORDER_ACCEPTED_UNVERIFIED",
                           "reason": "Provider ACK contained no order_id"}
        elif not callable(getter):
            observation = {"state": "ORDER_ACCEPTED_UNVERIFIED",
                           "order_id": order_id,
                           "reason": "No order-detail lookup on this rail"}
        else:
            try:
                observation = verified_order(getter(order_id), order_id)
            except Exception as exc:
                observation = {"state": "ORDER_ACCEPTED_UNVERIFIED",
                               "order_id": order_id,
                               "reason": f"Order verification unavailable: {type(exc).__name__}: {exc}"}
        state = str(observation["state"])
        if state == "FILLED_VERIFIED" and order_id:
            filler = getattr(self.rail, "order_fills", None)
            if callable(filler):
                try:
                    details = filler(order_id)
                    fills = details.get("fills") or []
                    observation["fill_records_count"] = len(fills)
                    observation["fill_commissions"] = [
                        {"trade_id": str(fill.get("trade_id") or ""),
                         "commission": fill.get("commission"),
                         "size": fill.get("size"),
                         "price": fill.get("price")}
                        for fill in fills if isinstance(fill, dict)
                    ]
                except Exception as exc:
                    observation["fill_lookup_warning"] = type(exc).__name__
        self.store.set_meta("last_coinbase_submission_state", state)
        self.store.set_meta("last_coinbase_submission_evidence",
                            json.dumps(observation))
        if state in {"FILLED_VERIFIED", "VENUE_NOT_FILLED"}:
            self.store.set_meta("pending_coinbase_order", "")
        if state not in {"FILLED_VERIFIED", "VENUE_NOT_FILLED"}:
            # Block subsequent dependent orders until Coinbase confirms the
            # fill or rejection. Never resubmit a possibly accepted order.
            self.store.set_meta("pending_coinbase_order", json.dumps({
                "order_id": order_id, "client_order_id": client_id,
                "product": product, "side": side,
                "submitted_at": time.time()}))
        return state, observation

    def _pending_order_decision(self, scope=None):
        """Reconcile accepted but unverified orders before placing another."""
        raw = self.store.get_meta("pending_coinbase_order")
        if not raw:
            return None
        pending = json.loads(raw)
        order_id = str(pending.get("order_id") or "")
        product = str(pending.get("product") or "")
        if not order_id:
            finder = getattr(self.rail, "find_order_id_by_client_id", None)
            if callable(finder):
                try:
                    order_id = finder(
                        product_id=product,
                        client_order_id=str(pending.get("client_order_id") or ""),
                    ) or ""
                except Exception as exc:
                    return Decision("HOLD", None, 0.0,
                        "Authenticated Coinbase client-order lookup failed: "
                        f"{type(exc).__name__}: {exc}. The original submission "
                        "remains unresolved and no retry will be sent."), "ORDER_AWAITING_VERIFICATION"
                if order_id:
                    pending["order_id"] = order_id
                    self.store.set_meta("pending_coinbase_order",
                                        json.dumps(pending))
            if not order_id:
                return Decision("HOLD", None, 0.0,
                    "Coinbase submission outcome remains unknown. No matching "
                    "order has been verified by client order ID; automatic "
                    "trading is paused until it can be reconciled."), "ORDER_AWAITING_VERIFICATION"
        getter = getattr(self.rail, "order_detail", None)
        if not callable(getter):
            return Decision("HOLD", None, 0.0,
                "Coinbase order-detail lookup unavailable; order may still be pending."), "ORDER_AWAITING_VERIFICATION"
        try:
            observed = verified_order(getter(order_id), order_id)
        except Exception as exc:
            observed = {"state": "ORDER_ACCEPTED_UNVERIFIED",
                        "reason": f"{type(exc).__name__}: {exc}"}
        self.store.set_meta("last_coinbase_submission_evidence", json.dumps(observed))
        state = observed["state"]
        if state in {"FILLED_VERIFIED", "VENUE_NOT_FILLED"}:
            self.store.set_meta("pending_coinbase_order", "")
            self.store.set_meta("last_coinbase_submission_state", state)
            if state == "FILLED_VERIFIED":
                # A completed SELL or BUY changes the position's lifecycle.
                # Reset observed peaks and cached entry costs so an old lot's
                # peak cannot mistakenly trigger a new position's stop.
                if str(pending.get("side") or "").upper() in {"BUY", "SELL"}:
                    for prefix in ("primary_peak:", "primary_fill_basis:",
                                   "primary_fill_basis_checked_at:"):
                        self.store.set_meta(
                            _position_meta_key(prefix, product,
                                               scope), "")
                if str(pending.get("side") or "").upper() == "SELL":
                    self.store.set_meta(
                        _position_meta_key("primary_maturity_at:", product,
                                           scope), "0")
                # Final receipt is appended separately from the original
                # submitted-order audit; never retroactively rewrite history.
                try:
                    base, quote = self._split_product(product)
                    self.audit.record_event(
                        event_type="coinbase_fill_reconciliation",
                        venue="coinbase", rail="coinbase-advanced",
                        network="coinbase", side=str(pending.get("side") or ""),
                        product_id=product, base_asset=base, quote_asset=quote,
                        base_quantity=observed.get("filled_size"),
                        quote_quantity=observed.get("filled_value"),
                        unit_price_quote=observed.get("average_filled_price"),
                        fee_quantity=observed.get("total_fees"),
                        order_id=order_id,
                        client_order_id=str(pending.get("client_order_id") or ""),
                        status="filled_verified",
                        source="coinbase_authenticated_order_detail",
                        raw_provider_response=observed)
                except Exception as exc:
                    self.store.add_ledger("AUDIT_ERROR", product, 0.0, 0.0, 0.0,
                        f"Coinbase filled; final audit receipt failed: {exc}")
                rationale = (f"Coinbase verified order {order_id} FILLED for {product}; "
                             f"base quantity {observed.get('filled_size')}, "
                             f"fees {observed.get('total_fees') or 'not yet reconciled'}. "
                             "Rescan balances before another order.")
            else:
                rationale = f"Coinbase order {order_id} did not fill: {observed.get('venue_status')}"
            return Decision("HOLD", None, 0.0, rationale), state
        return Decision("HOLD", None, 0.0,
            f"Awaiting authenticated Coinbase reconciliation for {product} order {order_id}. "
            f"Last check: {observed.get('reason','not filled yet')}."), "ORDER_AWAITING_VERIFICATION"

    def _primary_trade_step(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """AIscend, not BEAN, owns Coinbase Advanced live trading decisions.

        BEAN's hypothesis/reward research is separate and may be compared with
        this durable plan after execution. One confirmed order leg per cycle.
        """
        scope = snapshot.get("authenticated_scope")
        if callable(getattr(getattr(self, "rail", None), "account_scope_id", None)) and not scope:
            decision = Decision(
                "HOLD", None, 0.0,
                "Coinbase account identity unverified; live trading paused.")
            self.store.add_decision(decision, "ACCOUNT_SCOPE_UNVERIFIED")
            self.store.set_meta("primary_trade_plan_latest", json.dumps({
                "decision": "HOLD", "execution_status": "ACCOUNT_SCOPE_UNVERIFIED",
                "order_attempted": False, "decision_reason": decision.rationale,
                "evaluated_at": time.time()}))
            return self.sync()
        pending = self._pending_order_decision(scope=scope)
        if pending is not None:
            decision, status = pending
            self.store.add_decision(decision, status)
            self.store.set_meta("primary_trade_plan_latest", json.dumps({
                "evaluated_at": time.time(), "primary_engine": "aiscend_price_action_v1",
                "decision": decision.action, "decision_reason": decision.rationale,
                "execution_status": status, "order_attempted": False,
                "blocker": "coinbase_fill_reconciliation"}))
            return self.sync()
        # The REAL account and completed authenticated trade ledger are
        # consulted before learned BUY candidates are ranked. The first
        # observed bankroll is not confused with original deposited capital.
        try:
            account_evidence = evaluate_account(
                self.store, snapshot, getattr(self, "rail", None))
        except Exception as exc:
            account_evidence = {
                "capital": capital_snapshot(snapshot),
                "execution": {"status": "UNAVAILABLE", "products": {}},
                "read_status": "ACCOUNT_EVIDENCE_ERROR:" + type(exc).__name__,
                "realized_account_profit_verified": False,
            }
        self.store.set_meta("bean_account_latest",
                            json.dumps(account_evidence, sort_keys=True))
        fee_bps, fee_source = self._taker_cost_bps()
        # Actual BEAN Q feedback now competes for live funded allocation.
        # Its selected long/cash signals are not merely advisory telemetry.
        learned_plan = None
        learned_error = None
        if self.growth_gate is not None:
            try:
                cursor = int(self.store.get_meta("bean_rotation_cursor") or "0")
                learned_plan = evaluate_candidates(
                    self.growth_gate, snapshot, cursor=cursor,
                    account_history=account_evidence)
                self.store.set_meta(
                    "bean_rotation_cursor", str(learned_plan["next_cursor"]))
            except Exception as exc:
                learned_error = f"{type(exc).__name__}: {exc}"
        learned_rows = (learned_plan or {}).get("evaluated") or []
        plan = select_plan(
            snapshot, taker_bps=fee_bps, learned_candidates=learned_rows,
            account_history=account_evidence)
        learned_by_product = {
            str(row.get("product") or ""): row for row in learned_rows}
        positions = snapshot.get("positions") or {}
        rows = (snapshot.get("asset_universe") or {}).get("scored") or []
        eligible_routes = []
        for route in plan["routes"]:
            source = route.get("source_asset")
            if source:
                current_row = next(
                    (r for r in rows if str(r.get("product") or "") == source),
                    None,
                )
                hold_net = incumbent_hold_net_bps(
                    current_row, route["maturity_minutes"], taker_bps=fee_bps)
                if route["net_projected_bps"] <= max(0.0, hold_net) + ROTATION_MARGIN_BPS:
                    continue
            eligible_routes.append(route)
        winner = eligible_routes[0] if eligible_routes else None
        plan["rejected_rotation_routes"] = len(plan["routes"]) - len(eligible_routes)
        plan["winner"] = winner
        plan["evaluated_at"] = time.time()
        plan["primary_engine"] = "aiscend_price_action_v1"
        plan["fee_source"] = fee_source
        plan["fee_bps"] = fee_bps
        plan["bean_role"] = "q_policy_live_candidate_and_cash_exit"
        plan["bean_assessed_count"] = (learned_plan or {}).get("assessed_count", 0)
        plan["bean_live_signals"] = len(learned_rows)
        plan["account_capital_evidence"] = account_evidence["capital"]
        plan["authenticated_trade_history"] = {
            "status": account_evidence["execution"].get("status"),
            "read_status": account_evidence["read_status"],
            "fill_count_retained": account_evidence["execution"].get("fill_count_retained", 0),
            "products": account_evidence["execution"].get("products", {}),
            "realized_account_profit_verified": False,
            "account_scope_verified": account_evidence.get("account_scope_verified", False),
        }
        plan["bean_error"] = learned_error
        plan["order_attempted"] = False
        plan["holding_reviews"] = {}
        plan["decision_reason"] = None
        now = time.time()

        def write(decision, status):
            plan["decision"] = decision.action
            plan["decision_reason"] = decision.rationale
            plan["execution_status"] = status
            self.store.set_meta("primary_trade_plan_latest", json.dumps(plan))
            self.store.add_decision(decision, status)
            return self.sync()

        # Existing capital is the first-class input. Do not discard its
        # ownership or assume a low cash balance means no trading opportunities.
        for product, holding in positions.items():
            available = float(holding.get("available_qty",
                              holding.get("qty", 0)) or 0)
            if available <= 0:
                continue
            key = _position_meta_key("primary_maturity_at:", product, scope)
            deadline = float(self.store.get_meta(key) or "0")
            if not deadline:
                rows = (snapshot.get("asset_universe") or {}).get("scored") or []
                current_row = next((x for x in rows
                                    if str(x.get("product") or "") == product), {})
                signals = proposals(current_row)
                horizon = max((s["maturity_minutes"] for s in signals), default=60)
                deadline = now + horizon * 60
                self.store.set_meta(key, str(deadline))
            # Observe a real high-water mark on every trading cycle.
            mark = float(holding.get("mark_price") or
                         (snapshot.get("prices") or {}).get(product) or 0)
            peak_key = _position_meta_key("primary_peak:", product, scope)
            prior_peak = float(self.store.get_meta(peak_key) or "0")
            peak = max(prior_peak, mark) if math.isfinite(mark) and mark > 0 else prior_peak
            if peak > prior_peak:
                self.store.set_meta(peak_key, str(peak))

            # Reconstruct cost using authenticated Coinbase execution fills.
            # If transfers, missing history or incomplete pages prevent matching
            # the current balance, the basis is marked unknown, not invented.
            basis_key = _position_meta_key("primary_fill_basis:", product, scope)
            check_key = _position_meta_key("primary_fill_basis_checked_at:", product, scope)
            basis = json.loads(self.store.get_meta(basis_key) or "{}")
            last_checked = float(self.store.get_meta(check_key) or "0")
            fill_reader = getattr(getattr(self, "rail", None), "position_fills", None)
            if callable(fill_reader) and now - last_checked >= 300:
                try:
                    evidence = fill_reader(product)
                    basis = rebuild_position_basis(
                        product, holding.get("qty") or 0,
                        evidence["fills"], complete_history=evidence["complete"])
                except Exception as exc:
                    basis = {"cost_basis_status": "coinbase_fill_lookup_error",
                             "avg_cost": None, "reason": str(exc)[:200]}
                self.store.set_meta(basis_key, json.dumps(basis))
                self.store.set_meta(check_key, str(now))
            verified_cost = (basis.get("avg_cost")
                             if basis.get("cost_basis_status") == "verified_coinbase_fills"
                             else None)
            learned = learned_by_product.get(product)
            learned_exposure = (
                bool(learned["target_exposure"]) if learned is not None else None)
            issue = exit_reason(
                product, snapshot, deadline_reached=now >= deadline,
                current_mark_price=mark, observed_peak_price=peak,
                verified_entry_price=verified_cost,
                learned_exposure=learned_exposure)
            candidate = bool(winner and winner["source_asset"] == product)
            row = next((x for x in rows if str(x.get("product") or "") == product), None)
            plan["holding_reviews"][product] = {
                "mark": mark or None, "observed_peak": peak or None,
                "observed_drawdown_pct": round(100 * (1 - mark/peak), 3)
                    if mark > 0 and peak > 0 else None,
                "entry_price": verified_cost,
                "cost_basis_status": basis.get("cost_basis_status", "unavailable"),
                "return_1h": row.get("return_1h") if row else None,
                "return_6h": row.get("return_6h") if row else None,
                "bean_q_exposure": learned_exposure,
                "deadline_reached": now >= deadline,
                "exit_reason": issue, "rotation_candidate": candidate,
            }
            # Incumbent fee-adjusted horizon comparison occurs before winner selection.
            if issue or candidate:
                reason = issue or "better_net_route"
                target = winner["product"] if candidate else "cash"
                rationale = (f"AIscend {reason}: SELL {product} toward {target}; "
                             f"maturity={deadline:.0f}; "
                             f"route_net={winner['net_projected_bps'] if winner else 0:+.2f}bps; "
                             f"taker={fee_bps:.2f}bps.")
                decision = Decision("SELL", product, 1.0, rationale)
                plan["order_attempted"] = True
                try:
                    status = self._execute_sell(decision, snapshot) or "ORDER_ACCEPTED_UNVERIFIED"
                    if status == "FILLED_VERIFIED":
                        self.store.set_meta(key, "0")
                        self.store.set_meta(peak_key, "0")
                        self.store.set_meta(basis_key, "")
                        self.store.set_meta(check_key, "0")
                except Exception as exc:
                    status = ("ORDER_SUBMISSION_UNKNOWN"
                              if self.store.get_meta("pending_coinbase_order")
                              else "EXECUTION_ERROR")
                    decision = Decision("SELL", product, 1.0,
                                        rationale + f" Execution failed: {exc}")
                return write(decision, status)

        if winner is None:
            skipped = plan.get("skipped") or {}
            rationale = (
                "AIscend HOLD: no cost-positive executable entry in "
                f"{plan['examined']} scanned assets; "
                f"signals={plan['candidate_signals']}, "
                f"routes={plan['costed_routes']}, "
                f"below_cost={skipped.get('negative_net_edge',0)}, "
                f"no_quote_path={skipped.get('no_quote_path',0)}. "
                "Current holdings evaluated separately for exits: "
                + json.dumps(plan["holding_reviews"], sort_keys=True)[:900]
            )
            return write(Decision("HOLD", None, 0.0, rationale), "HELD")

        source_asset = winner.get("source_asset")
        product = winner["product"]
        target_quote = self._split_product(product)[1]
        source_quote = winner["source_quote"]
        if source_asset:
            # The incumbent was evaluated above; if its rotation is truly the
            # best eligible funded route, its SELL should already have run.
            # This condition is a detected planning defect, never a fake HOLD.
            reason = (f"Live rotation could not sell {source_asset} to fund "
                      f"{product}; route selection is inconsistent.")
            return write(Decision("HOLD", None, 0.0, reason), "PLAN_ERROR")

        if source_quote != target_quote:
            rationale = (f"AIscend cross-quote route toward {product}: "
                         f"{source_quote}->{target_quote}; net "
                         f"{winner['net_projected_bps']:+.2f}bps after all legs.")
            plan["order_attempted"] = True
            try:
                decision = self._execute_quote_bridge(
                    source_quote, target_quote, snapshot, rationale)
                status = (self.store.get_meta("last_coinbase_submission_state")
                          or "ORDER_ACCEPTED_UNVERIFIED")
            except Exception as exc:
                status = ("ORDER_SUBMISSION_UNKNOWN"
                          if self.store.get_meta("pending_coinbase_order")
                          else "EXECUTION_ERROR")
                decision = Decision("HOLD", None, 0.0,
                                    rationale + f" Conversion failed: {exc}")
            return write(decision, status)

        decision = Decision("BUY", product, 1.0,
            f"AIscend {winner['strategy']} BUY {product}; estimated gross "
            f"{winner['estimated_growth_bps']:+.1f}bps, all-in route cost "
            f"{winner['total_route_cost_bps']:.1f}bps, net "
            f"{winner['net_projected_bps']:+.1f}bps; provisional "
            f"maturity {winner['maturity_minutes']}m.")
        plan["order_attempted"] = True
        try:
            status = self._execute_buy(decision, snapshot) or "ORDER_ACCEPTED_UNVERIFIED"
            if status in {"FILLED_VERIFIED", "ORDER_ACCEPTED_UNVERIFIED"}:
                self.store.set_meta(_position_meta_key("primary_maturity_at:", product, scope),
                                    str(now + 60 * winner["maturity_minutes"]))
                if status == "FILLED_VERIFIED":
                    for prefix in ("primary_peak:", "primary_fill_basis:",
                                   "primary_fill_basis_checked_at:"):
                        self.store.set_meta(_position_meta_key(prefix, product, scope), "")
        except Exception as exc:
            # Transport failures and ambiguous provider responses may have
            # placed an order. Make unresolved state visible, never "failed".
            status = ("ORDER_SUBMISSION_UNKNOWN"
                      if self.store.get_meta("pending_coinbase_order")
                      else "EXECUTION_ERROR")
            decision = Decision("BUY", product, 1.0,
                                decision.rationale + f" Execution failed: {exc}")
        return write(decision, status)

    def step(self) -> dict[str, Any]:
        """Run AIscend trading without synchronous BEAN research or veto."""
        before = self.sync()
        return self._primary_trade_step(before)

    def _execute_buy(self, decision: Decision, snapshot: dict[str, Any]) -> None:
        product_id = str(decision.symbol)
        _, quote = self._split_product(product_id)
        available = Decimal(str((snapshot.get("cash_by_quote") or {}).get(quote, 0.0)))
        spend = available * Decimal(str(decision.fraction))
        # Trade the requested fraction of spendable quote balance.
        # No hidden portfolio-percentage cap: the experiment's exposure is
        # controlled by its actual decision, available funds and venue sizing.
        # Coinbase requires each market's quote increment, including when
        # spending the full quote balance. Rounding down is exchange sizing,
        # not a policy cash reserve.
        product_rules = self.rail.product(product_id)
        increment = Decimal(str(product_rules.get("quote_increment") or "0.00000001"))
        if increment <= 0 or not increment.is_finite():
            raise RuntimeError(f"Invalid Coinbase quote increment for {product_id}.")
        spend = (spend / increment).to_integral_value(rounding=ROUND_DOWN) * increment
        minimum = Decimal(str(product_rules.get("quote_min_size") or "0"))
        if spend <= 0 or spend < minimum:
            raise RuntimeError(f"Available {quote} is below Coinbase order minimum for {product_id}.")

        preview = self.rail.preview_market_buy(product_id, spend)
        client_order_id = f"aiscend-{uuid.uuid4()}"
        self._record_order_intent(product=product_id, side="BUY",
                                  client_id=client_order_id)
        try:
            order = self.rail.market_buy(
                product_id, spend, client_order_id=client_order_id)
        except CoinbaseExplicitRejection:
            # Coinbase explicitly rejected the order; no possible accepted fill.
            self.store.set_meta("pending_coinbase_order", "")
            self.store.set_meta("last_coinbase_submission_state", "VENUE_REJECTED")
            raise
        # All other transport, SDK and unconfirmed-response failures retain
        # the previously recorded client_order_id. We cannot safely retry.
        try:
            require_accepted_order(order)
        except CoinbaseExplicitRejection:
            self.store.set_meta("pending_coinbase_order", "")
            self.store.set_meta("last_coinbase_submission_state", "VENUE_REJECTED")
            raise
        result_state, evidence = self._check_or_track_submission(
            order, product=product_id, side="BUY", client_id=client_order_id)
        self.store.add_ledger(
            "ADVANCED_ORDER_SUBMITTED_BUY",
            product_id,
            0.0,
            0.0,
            -float(spend),
            f"{decision.rationale} | preview={str(preview)[:220]} | order={str(order)[:220]}",
        )

        try:
            base, quote = self._split_product(product_id)
            actual_base = evidence.get("filled_size") if result_state == "FILLED_VERIFIED" else None
            actual_quote = evidence.get("filled_value") if result_state == "FILLED_VERIFIED" else None
            actual_price = evidence.get("average_filled_price") if result_state == "FILLED_VERIFIED" else None
            self.audit.record_trade(
                venue="coinbase",
                rail="coinbase-advanced",
                network="coinbase",
                side="BUY",
                product_id=product_id,
                base_asset=base,
                quote_asset=quote,
                base_quantity=actual_base,
                quote_quantity=actual_quote,
                unit_price_quote=actual_price,
                fee_quantity=evidence.get("total_fees") if result_state == "FILLED_VERIFIED" else None,
                order_id=self.audit.provider_id(order, "order_id", "orderId"),
                client_order_id=client_order_id,
                status=result_state.lower(),
                rationale=decision.rationale,
                source="coinbase_advanced_order_response",
                raw_provider_response={"preview": preview, "order": order, "verification": evidence},
            )
        except Exception as exc:
            self.store.add_ledger(
                "AUDIT_ERROR",
                product_id,
                0.0,
                0.0,
                0.0,
                f"Coinbase BUY submission accepted but audit write failed: {exc}",
            )


        return result_state
    def _execute_sell(self, decision: Decision, snapshot: dict[str, Any]) -> None:
        product_id = str(decision.symbol)
        position = (snapshot.get("positions") or {}).get(product_id) or {}
        qty = Decimal(str(position.get("available_qty", position.get("qty", 0.0)) or 0.0)) * Decimal(str(decision.fraction))
        product_rules = self.rail.product(product_id)
        increment = Decimal(str(product_rules.get("base_increment") or "0.00000001"))
        if increment <= 0 or not increment.is_finite():
            raise RuntimeError(f"Invalid Coinbase base increment for {product_id}.")
        qty = (qty / increment).to_integral_value(rounding=ROUND_DOWN) * increment
        minimum = Decimal(str(product_rules.get("base_min_size") or "0"))
        if qty <= 0 or qty < minimum:
            raise RuntimeError(f"No executable {product_id} position available to sell.")

        # Coinbase previews expose provider-side fees and reject unavailable
        # sizes before any live liquidation, including USDC/USD bridges.
        previewer = getattr(self.rail, "preview_market_sell", None)
        preview = previewer(product_id, qty) if callable(previewer) else None

        client_order_id = f"aiscend-{uuid.uuid4()}"
        self._record_order_intent(product=product_id, side="SELL",
                                  client_id=client_order_id)
        try:
            order = self.rail.market_sell(
                product_id, qty, client_order_id=client_order_id)
        except CoinbaseExplicitRejection:
            # Coinbase explicitly rejected the order; no possible accepted fill.
            self.store.set_meta("pending_coinbase_order", "")
            self.store.set_meta("last_coinbase_submission_state", "VENUE_REJECTED")
            raise
        # All other transport, SDK and unconfirmed-response failures retain
        # the previously recorded client_order_id. We cannot safely retry.
        try:
            require_accepted_order(order)
        except CoinbaseExplicitRejection:
            self.store.set_meta("pending_coinbase_order", "")
            self.store.set_meta("last_coinbase_submission_state", "VENUE_REJECTED")
            raise
        result_state, evidence = self._check_or_track_submission(
            order, product=product_id, side="SELL", client_id=client_order_id)
        self.store.add_ledger(
            "ADVANCED_ORDER_SUBMITTED_SELL",
            product_id,
            float(qty),
            0.0,
            0.0,
            f"{decision.rationale} | preview={str(preview)[:220]} | order={str(order)[:220]}",
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
                base_quantity=evidence.get("filled_size") if result_state == "FILLED_VERIFIED" else None,
                quote_quantity=evidence.get("filled_value") if result_state == "FILLED_VERIFIED" else None,
                fee_quantity=evidence.get("total_fees") if result_state == "FILLED_VERIFIED" else None,
                order_id=self.audit.provider_id(order, "order_id", "orderId"),
                client_order_id=client_order_id,
                status=result_state.lower(),
                rationale=decision.rationale,
                source="coinbase_advanced_order_response",
                raw_provider_response={"preview": preview, "order": order, "verification": evidence},
            )
        except Exception as exc:
            self.store.add_ledger(
                "AUDIT_ERROR",
                product_id,
                0.0,
                0.0,
                0.0,
                f"Coinbase SELL submission accepted but audit write failed: {exc}",
            )


        return result_state
