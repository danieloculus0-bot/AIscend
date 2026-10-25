import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore
from src.wallets.advanced_trade import AdvancedBalance, AdvancedTradeStatus


class FakeRail:
    def __init__(self):
        self.usdc = Decimal("25.00")
        self.aleo = Decimal("0")
        self.buys = []

    def status(self):
        balances = [AdvancedBalance("USDC", self.usdc, Decimal("0"))]
        if self.aleo > 0:
            balances.append(AdvancedBalance("ALEO", self.aleo, Decimal("0")))
        return AdvancedTradeStatus(
            configured=True,
            balances=tuple(balances),
            tradable_spot_products=2,
            product_ids=("ALEO-USDC", "ETH-USDC"),
        )

    def product(self, product_id):
        return {"product_id": product_id, "price": "2.00"}

    def preview_market_buy(self, product_id, quote_size):
        return {"preview_id": "preview", "product_id": product_id, "quote_size": str(quote_size)}

    def market_buy(self, product_id, quote_size, client_order_id):
        spend = Decimal(str(quote_size))
        self.usdc -= spend
        if product_id == "ALEO-USDC":
            self.aleo += spend / Decimal("2")
        self.buys.append((product_id, spend, client_order_id))
        return {"success": True, "order_id": "buy-1"}

    def market_sell(self, product_id, base_size, client_order_id):
        qty = Decimal(str(base_size))
        if product_id == "ALEO-USDC":
            self.aleo -= qty
            self.usdc += qty * Decimal("2")
        return {"success": True, "order_id": "sell-1"}


class FakeListingSentinel:
    def __init__(self, hot=None):
        self.hot = hot or []

    def poll(self):
        return {
            "available": True,
            "product_count": 2,
            "new_products": [],
            "changes": [],
            "hot": list(self.hot),
            "errors": [],
        }


class FakeHumanSignals:
    available = True
    raw_fomo_index = 50.0
    fomo_index = 50.0
    crowd_regime = "BALANCED"
    fear_greed_value = 50.0
    fear_greed_classification = "NEUTRAL"
    fear_greed_change_1d = 0.0
    news_sentiment = 0.0
    social_sentiment = 0.0
    politics_sentiment = 0.0
    politics_risk = 0.0
    weirdness = 0.0
    news_attention = 0.0
    social_attention = 0.0
    news_coverage = 1.0
    social_coverage = 1.0
    politics_coverage = 1.0
    social_source_diversity = 1.0
    social_confidence = 1.0
    human_signal_quality = 1.0
    source_counts = {"news": 1, "social": 1}
    source_status = {"news": "test", "social": "test"}
    top_headlines = ()
    errors = ()

    def as_dict(self):
        return {
            key: getattr(self, key)
            for key in (
                "available",
                "raw_fomo_index",
                "fomo_index",
                "crowd_regime",
                "fear_greed_value",
                "fear_greed_classification",
                "fear_greed_change_1d",
                "news_sentiment",
                "social_sentiment",
                "politics_sentiment",
                "politics_risk",
                "weirdness",
                "news_attention",
                "social_attention",
                "news_coverage",
                "social_coverage",
                "politics_coverage",
                "social_source_diversity",
                "social_confidence",
                "human_signal_quality",
                "source_counts",
                "source_status",
                "top_headlines",
                "errors",
            )
        }


class FakeHumanResearch:
    def __init__(self, signals=None):
        self.signals = signals or FakeHumanSignals()

    def collect(self):
        return self.signals


class FakeUniverse:
    def collect(self, priority_products=None):
        return {
            "available": True,
            "product_count": 2,
            "scanned_count": 2,
            "errors": [],
            "top": [
                {
                    "product": "ALEO-USDC",
                    "base": "ALEO",
                    "quote": "USDC",
                    "price": 2.0,
                    "return_5m": 0.01,
                    "return_1h": 0.03,
                    "return_6h": 0.08,
                    "volume_ratio": 1.6,
                    "spread_bps": 4.0,
                    "score": 0.72,
                },
                {
                    "product": "ETH-USDC",
                    "base": "ETH",
                    "quote": "USDC",
                    "price": 3000.0,
                    "return_5m": 0.0,
                    "return_1h": 0.0,
                    "return_6h": 0.0,
                    "volume_ratio": 1.0,
                    "spread_bps": 1.0,
                    "score": 0.02,
                },
            ],
        }


class AdvancedLiveTests(unittest.TestCase):
    def test_coinbase_key_rotation_during_status_discards_mixed_account_snapshot(self):
        class RotatingRail(FakeRail):
            def __init__(self):
                super().__init__()
                self.scope = "a" * 64
                self.status_calls = 0

            def account_scope_id(self):
                return self.scope

            def status(self):
                self.status_calls += 1
                # Wallets are returned under A; the key changes to B before
                # the caller can label those balances and authorize an order.
                result = super().status()
                self.scope = "b" * 64
                return result

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "mid-snapshot-key-rotation.db")
            try:
                rail = RotatingRail()
                engine = AdvancedSpotEngine(
                    store, rail=rail, asset_universe=FakeUniverse(),
                    listing_sentinel=FakeListingSentinel(),
                    human_researcher=FakeHumanResearch(),
                )
                with self.assertRaisesRegex(
                    RuntimeError, "credentials changed during authenticated account snapshot"
                ):
                    engine.sync()
                self.assertEqual(rail.status_calls, 1)
                self.assertIsNone(engine._snapshot)
                self.assertEqual(rail.buys, [])
            finally:
                store.close()

    def test_coinbase_scope_reader_failure_before_status_never_uses_cash(self):
        class BrokenScopeRail(FakeRail):
            def __init__(self):
                super().__init__()
                self.status_calls = 0

            def account_scope_id(self):
                raise RuntimeError("credential store unavailable")

            def status(self):
                self.status_calls += 1
                return super().status()

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "unavailable-credential-scope.db")
            try:
                rail = BrokenScopeRail()
                engine = AdvancedSpotEngine(
                    store, rail=rail, asset_universe=FakeUniverse(),
                    listing_sentinel=FakeListingSentinel(),
                    human_researcher=FakeHumanResearch(),
                )
                with self.assertRaisesRegex(
                    RuntimeError, "credential identity unavailable before account snapshot"
                ):
                    engine.sync()
                self.assertEqual(rail.status_calls, 0)
                self.assertIsNone(engine._snapshot)
                self.assertEqual(rail.buys, [])
            finally:
                store.close()

    def test_authentication_key_switch_resets_starting_equity_baseline(self):
        class ScopedRail(FakeRail):
            scope = "a" * 64
            def account_scope_id(self):
                return self.scope

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = ScopedRail()
            engine = AdvancedSpotEngine(
                store, rail=rail, asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            account_a = engine.sync()
            self.assertEqual(account_a["starting_cash"], 25.0)
            self.assertEqual(account_a["authenticated_scope"], "a" * 64)
            rail.scope = "b" * 64
            rail.usdc = Decimal("80")
            account_b = engine.sync()
            self.assertEqual(account_b["starting_cash"], 80.0)
            self.assertEqual(account_b["authenticated_scope"], "b" * 64)
            rail.scope = "a" * 64
            restored = engine.sync()
            self.assertEqual(restored["starting_cash"], 25.0)
            self.assertNotEqual(restored["starting_cash"], account_b["starting_cash"])
            self.assertEqual(
                store.get_meta("advanced_starting_value:" + "a"*64), "25.0000000000")
            self.assertEqual(
                store.get_meta("advanced_starting_value:" + "b"*64), "80.0000000000")
            store.close()

    def test_live_position_state_isolated_between_coinbase_keys(self):
        class ScopedRail(FakeRail):
            scope = "a" * 64

            def account_scope_id(self):
                return self.scope

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = ScopedRail()
            rail.aleo = Decimal("3")
            engine = AdvancedSpotEngine(
                store, rail=rail, asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            product = "ALEO-USDC"
            a = "a" * 64
            b = "b" * 64
            store.set_meta(f"primary_peak:{a}:{product}", "99")
            store.set_meta(f"primary_peak_unit:{a}:{product}", "USDC")
            store.set_meta(f"primary_maturity_at:{a}:{product}", "1")
            store.set_meta(f"primary_fill_basis:{a}:{product}", json.dumps({
                "cost_basis_status": "verified_coinbase_fills", "avg_cost": 1.5}))
            store.set_meta(f"primary_fill_basis_checked_at:{a}:{product}", "9999999999")

            no_entry = {"routes": [], "examined": 0, "candidate_signals": 0,
                        "costed_routes": 0, "skipped": {}}
            with patch("src.advanced_live.select_plan", return_value=no_entry), \
                 patch("src.advanced_live.exit_reason", return_value=None):
                engine._primary_trade_step(engine.sync())
                first = json.loads(store.get_meta("primary_trade_plan_latest"))
                self.assertEqual(first["holding_reviews"][product]["observed_peak"], 99)
                self.assertEqual(first["holding_reviews"][product]["entry_price"], 1.5)
                rail.scope = b
                engine._primary_trade_step(engine.sync())
                second = json.loads(store.get_meta("primary_trade_plan_latest"))
                self.assertEqual(second["holding_reviews"][product]["observed_peak"], 2.0)
                self.assertIsNone(second["holding_reviews"][product]["entry_price"])
                self.assertFalse(second["holding_reviews"][product]["deadline_reached"])
            self.assertEqual(store.get_meta(f"primary_peak:{a}:{product}"), "99")
            self.assertEqual(store.get_meta(f"primary_peak:{b}:{product}"), "2.0")
            self.assertIsNone(store.get_meta("primary_peak:" + product))
            self.assertFalse(rail.buys)
            store.close()

    def test_unverified_coinbase_scope_blocks_live_order_submission(self):
        class UnidentifiedRail(FakeRail):
            def account_scope_id(self):
                return None

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = UnidentifiedRail()
            engine = AdvancedSpotEngine(
                store, rail=rail, asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            engine.step()
            self.assertFalse(rail.buys)
            self.assertEqual(
                store.latest_decisions(1)[0]["status"], "ACCOUNT_SCOPE_UNVERIFIED")
            store.close()

    def test_hourly_bean_exam_cannot_veto_advanced_opportunity(self):
        class BeanDisagrees:
            def assess(self):
                return {"status": "READY", "experiment": 66,
                        "target": "ETH-USD", "bars": 75}

            def permits_buy(self, policy, symbol):
                return symbol == "ETH-USDC"

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = FakeRail()
            engine = AdvancedSpotEngine(
                store, rail=rail, asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
                policy_gate=BeanDisagrees(),
            )
            after = engine.step()
            row = store.latest_decisions(1)[0]
            self.assertEqual(row["action"], "BUY")
            self.assertEqual(row["status"], "ORDER_ACCEPTED_UNVERIFIED")
            self.assertEqual(after["cash"], 0.0)
            self.assertTrue(rail.buys)
            self.assertEqual(rail.buys[0][1], Decimal("25.00"))
            store.close()

    def test_existing_holdings_are_forced_into_each_market_scan(self):
        class ObservedUniverse(FakeUniverse):
            seen = None
            def collect(self, priority_products=None):
                self.seen = list(priority_products or [])
                return super().collect(priority_products=priority_products)

        class HeldRail(FakeRail):
            def status(self):
                return AdvancedTradeStatus(
                    configured=True,
                    balances=(AdvancedBalance("USDC", Decimal("0.9"), Decimal("0")),
                              AdvancedBalance("GROVE", Decimal("10"), Decimal("0"))),
                    tradable_spot_products=2,
                    product_ids=("GROVE-USDC", "ALEO-USDC"))

        with tempfile.TemporaryDirectory() as td:
            store=StateStore(Path(td)/"held.db")
            try:
                universe=ObservedUniverse()
                engine=AdvancedSpotEngine(
                    store,rail=HeldRail(),asset_universe=universe,
                    listing_sentinel=FakeListingSentinel(),
                    human_researcher=FakeHumanResearch())
                engine.sync()
                self.assertIn("GROVE-USDC", universe.seen)
            finally:
                store.close()

    def test_no_candle_evidence_does_not_fake_trade_signal(self):
        class BeanUnavailable:
            def assess(self):
                return {"status": "DATA_UNAVAILABLE", "trade_effect": "NONE"}

            def permits_buy(self, policy, symbol):
                return True

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = FakeRail()
            engine = AdvancedSpotEngine(
                store, rail=rail, asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
                policy_gate=BeanUnavailable(),
            )
            engine.step()
            self.assertTrue(rail.buys)
            self.assertEqual(store.latest_decisions(1)[0]["status"], "ORDER_ACCEPTED_UNVERIFIED")
            store.close()

    def test_engine_selects_best_tradable_coinbase_spot_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = FakeRail()
            engine = AdvancedSpotEngine(
                store,
                rail=rail,
                asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            snap = engine.step()
            rows = store.latest_decisions(1)
            self.assertEqual(rows[0]["action"], "BUY")
            self.assertEqual(rows[0]["symbol"], "ALEO-USDC")
            self.assertEqual(rows[0]["status"], "ORDER_ACCEPTED_UNVERIFIED")
            self.assertTrue(rail.buys)
            self.assertLess(snap["cash"], 25.0)
            store.close()

    def test_engine_can_buy_non_usd_quote_product(self):
        class EurRail(FakeRail):
            def __init__(self):
                super().__init__()
                self.usdc = Decimal("0")
                self.eur = Decimal("20")
                self.meme = Decimal("0")

            def status(self):
                balances = [AdvancedBalance("EUR", self.eur, Decimal("0"))]
                if self.meme > 0:
                    balances.append(AdvancedBalance("MEME", self.meme, Decimal("0")))
                return AdvancedTradeStatus(
                    configured=True,
                    balances=tuple(balances),
                    tradable_spot_products=3,
                    product_ids=("MEME-EUR", "BTC-EUR", "BTC-USD"),
                )

            def product(self, product_id):
                prices = {
                    "MEME-EUR": "0.20",
                    "BTC-EUR": "50000",
                    "BTC-USD": "55000",
                }
                return {"product_id": product_id, "price": prices[product_id]}

            def market_buy(self, product_id, quote_size, client_order_id):
                spend = Decimal(str(quote_size))
                self.eur -= spend
                self.meme += spend / Decimal("0.20")
                self.buys.append((product_id, spend, client_order_id))
                return {"success": True, "order_id": "meme-buy"}

        class EurUniverse:
            def collect(self, priority_products=None):
                return {
                    "available": True,
                    "product_count": 1,
                    "scanned_count": 1,
                    "errors": [],
                    "top": [{
                        "product": "MEME-EUR",
                        "base": "MEME",
                        "quote": "EUR",
                        "price": 0.20,
                        "return_5m": 0.02,
                        "return_1h": 0.04,
                        "return_6h": 0.10,
                        "volume_ratio": 2.0,
                        "spread_bps": 8.0,
                        "score": 0.75,
                    }],
                }

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = EurRail()
            engine = AdvancedSpotEngine(
                store,
                rail=rail,
                asset_universe=EurUniverse(),
                listing_sentinel=FakeListingSentinel(),
            )
            snap = engine.step()
            row = store.latest_decisions(1)[0]
            self.assertEqual(row["action"], "BUY")
            self.assertEqual(row["symbol"], "MEME-EUR")
            self.assertEqual(row["status"], "ORDER_ACCEPTED_UNVERIFIED")
            self.assertTrue(rail.buys)
            self.assertGreater(snap["net_liquidation"], 20.0)
            self.assertEqual(snap["balance_values_usd"].get("EUR", 0.0), 0.0)
            self.assertGreater(snap["balance_values_usd"].get("MEME", 0.0), 0.0)
            store.close()

    def test_engine_uses_full_scored_universe_not_absolute_ranked_top(self):
        class FullUniverse:
            def collect(self, priority_products=None):
                negative={
                    "product":"ETH-USDC",
                    "base":"ETH",
                    "quote":"USDC",
                    "price":3000.0,
                    "return_5m":-0.03,
                    "return_1h":-0.05,
                    "return_6h":-0.08,
                    "volume_ratio":2.0,
                    "spread_bps":2.0,
                    "score":-0.85,
                }
                bullish={
                    "product":"ALEO-USDC",
                    "base":"ALEO",
                    "quote":"USDC",
                    "price":2.0,
                    "return_5m":0.02,
                    "return_1h":0.04,
                    "return_6h":0.09,
                    "volume_ratio":2.0,
                    "spread_bps":3.0,
                    "score":0.72,
                }
                return {
                    "available":True,
                    "product_count":2,
                    "scanned_count":2,
                    "errors":[],
                    "top":[negative],
                    "buy_top":[bullish,negative],
                    "scored":[negative,bullish],
                }

        with tempfile.TemporaryDirectory() as td:
            store=StateStore(Path(td) / "advanced.db")
            rail=FakeRail()
            engine=AdvancedSpotEngine(
                store,
                rail=rail,
                asset_universe=FullUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            engine.step()
            row=store.latest_decisions(1)[0]
            self.assertEqual(row["action"],"BUY")
            self.assertEqual(row["symbol"],"ALEO-USDC")
            store.close()

    def test_fresh_public_listing_signal_can_trigger_candidate(self):
        class FreshRail(FakeRail):
            def status(self):
                return AdvancedTradeStatus(
                    configured=True,
                    balances=(AdvancedBalance("USDC", self.usdc, Decimal("0")),),
                    tradable_spot_products=1,
                    product_ids=("SHIT-USDC",),
                )

            def market_buy(self, product_id, quote_size, client_order_id):
                spend = Decimal(str(quote_size))
                self.usdc -= spend
                self.buys.append((product_id, spend, client_order_id))
                return {"success": True, "order_id": "fresh-buy"}

        class FreshUniverse:
            def collect(self, priority_products=None):
                return {
                    "available": True,
                    "product_count": 1,
                    "scanned_count": 1,
                    "errors": [],
                    "top": [{
                        "product": "SHIT-USDC",
                        "base": "SHIT",
                        "quote": "USDC",
                        "price": 0.01,
                        "return_5m": 0.06,
                        "return_1h": 0.09,
                        "return_6h": 0.0,
                        "volume_ratio": 4.0,
                        "spread_bps": 12.0,
                        "score": 0.0,
                    }],
                }

        event = {
            "ts": __import__("time").time(),
            "product": "SHIT-USDC",
            "base": "SHIT",
            "quote": "USDC",
            "event": "NEW_PRODUCT",
            "stage": "FULL_TRADING",
        }

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = FreshRail()
            engine = AdvancedSpotEngine(
                store,
                rail=rail,
                asset_universe=FreshUniverse(),
                listing_sentinel=FakeListingSentinel(hot=[event]),
                human_researcher=FakeHumanResearch(),
            )
            engine.step()
            row = store.latest_decisions(1)[0]
            self.assertEqual(row["action"], "BUY")
            self.assertEqual(row["symbol"], "SHIT-USDC")
            self.assertEqual(row["status"], "ORDER_ACCEPTED_UNVERIFIED")
            self.assertIn("breakout BUY", row["rationale"])
            store.close()

    def test_human_weather_populates_and_fuses_advanced_score(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            engine = AdvancedSpotEngine(
                store,
                rail=FakeRail(),
                asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            snap = engine.sync()
            best = snap["market_radar"]["best_long"]
            human = snap["research"]["human"]

            self.assertTrue(human["available"])
            self.assertEqual(human["crowd_regime"], "ENGAGED")
            self.assertGreater(best["human_weight"], 0.0)
            self.assertNotEqual(
                best["decision_score"],
                best["pre_human_score"],
            )
            self.assertIn("Human Weather", snap["research"]["thesis"])
            store.close()

    def test_provider_rejection_does_not_enter_executed_ledger(self):
        class RejectingRail(FakeRail):
            def market_buy(self, product_id, quote_size, client_order_id):
                return {"success": False, "error_response": {"error": "MINIMUM_NOT_MET"}}
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            engine = AdvancedSpotEngine(
                store, rail=RejectingRail(), asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            engine.step()
            record = store.latest_decisions(1)[0]
            self.assertEqual(record["status"], "EXECUTION_ERROR")
            self.assertIn("not confirmed", record["rationale"])
            self.assertFalse(any(row["kind"] == "ADVANCED_ORDER_SUBMITTED_BUY"
                                 for row in store.latest_ledger(10)))
            store.close()

    def test_balance_holds_remain_in_net_value_not_spendable_cash(self):
        class HoldingRail(FakeRail):
            def status(self):
                return AdvancedTradeStatus(
                    configured=True, tradable_spot_products=2,
                    product_ids=("ALEO-USDC", "ETH-USDC"),
                    balances=(
                        AdvancedBalance("USDC", Decimal("20"), Decimal("5")),
                        AdvancedBalance("ALEO", Decimal("1"), Decimal("2")),
                    ),
                )
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            engine = AdvancedSpotEngine(
                store, rail=HoldingRail(), asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
                human_researcher=FakeHumanResearch(),
            )
            snap = engine.sync()
            self.assertEqual(snap["cash_by_quote"]["USDC"], 20.0)
            self.assertEqual(snap["cash"], 25.0)
            self.assertEqual(snap["positions"]["ALEO-USDC"]["qty"], 3.0)
            self.assertEqual(snap["positions"]["ALEO-USDC"]["available_qty"], 1.0)
            self.assertIsNone(snap["positions"]["ALEO-USDC"]["avg_cost"])
            self.assertEqual(snap["positions"]["ALEO-USDC"]["mark_price"], 2.0)
            self.assertEqual(snap["positions"]["ALEO-USDC"]["cost_basis_status"],
                             "unverified_without_fills")
            self.assertEqual(snap["net_liquidation"], 31.0)
            self.assertTrue(snap["valuation_complete"])
            store.close()

    def test_execution_failure_still_hits_decision_journal(self):
        class BrokenRail(FakeRail):
            def preview_market_buy(self, product_id, quote_size):
                raise RuntimeError("preview rejected")

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            engine = AdvancedSpotEngine(
                store,
                rail=BrokenRail(),
                asset_universe=FakeUniverse(),
                listing_sentinel=FakeListingSentinel(),
            )
            engine.step()
            row = store.latest_decisions(1)[0]
            self.assertEqual(row["action"], "BUY")
            self.assertEqual(row["status"], "EXECUTION_ERROR")
            self.assertIn("preview rejected", row["rationale"])
            store.close()


    def test_non_dollar_position_mark_uses_quote_units_and_migrates_legacy_peak(self):
        from src.primary_strategy import exit_reason

        class EuroRail(FakeRail):
            def account_scope_id(self):
                return "c" * 64
            def status(self):
                return AdvancedTradeStatus(
                    configured=True,
                    balances=(AdvancedBalance("MEME", Decimal("10"), Decimal("0")),),
                    tradable_spot_products=3,
                    product_ids=("MEME-EUR", "BTC-EUR", "BTC-USD"),
                )
            def product(self, product_id):
                return {"product_id": product_id, "price": {
                    "MEME-EUR": "0.20", "BTC-EUR": "50000", "BTC-USD": "55000"
                }[product_id]}

        class EuroUniverse:
            def collect(self, priority_products=None):
                return {"available": True, "scored": [
                    {"product": "MEME-EUR", "price": 0.20,
                     "spread_bps": 5, "return_5m": 0,
                     "return_1h": 0, "return_6h": 0}
                ], "top": [], "errors": []}

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "quote.db")
            try:
                rail = EuroRail()
                engine = AdvancedSpotEngine(
                    store, rail=rail, asset_universe=EuroUniverse(),
                    listing_sentinel=FakeListingSentinel(),
                    human_researcher=FakeHumanResearch())
                snap = engine.sync()
                self.assertAlmostEqual(snap["positions"]["MEME-EUR"]["mark_price"], 0.20)
                self.assertEqual(snap["positions"]["MEME-EUR"]["mark_quote_currency"], "EUR")
                self.assertAlmostEqual(snap["balance_values_usd"]["MEME"], 2.20)
                self.assertEqual(exit_reason(
                    "MEME-EUR", snap, current_mark_price=0.20,
                    verified_entry_price=0.23), "verified_entry_loss_limit")

                peak_key = "primary_peak:" + "c"*64 + ":MEME-EUR"
                unit_key = "primary_peak_unit:" + "c"*64 + ":MEME-EUR"
                store.set_meta(peak_key, "99")
                no_entry = {"routes": [], "examined": 0,
                            "candidate_signals": 0, "costed_routes": 0,
                            "skipped": {}}
                with patch("src.advanced_live.select_plan", return_value=no_entry), \
                     patch("src.advanced_live.exit_reason", return_value=None):
                    engine._primary_trade_step(snap)
                self.assertEqual(store.get_meta(unit_key), "EUR")
                self.assertEqual(store.get_meta(peak_key), "0.2")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
