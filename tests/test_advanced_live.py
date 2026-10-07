import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

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
            self.assertEqual(rows[0]["status"], "LIVE_EXECUTED")
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
            self.assertEqual(row["status"], "LIVE_EXECUTED")
            self.assertTrue(rail.buys)
            self.assertGreater(snap["net_liquidation"], 20.0)
            self.assertGreater(snap["balance_values_usd"].get("EUR", 0.0), 0.0)
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
                        "return_5m": 0.0,
                        "return_1h": 0.0,
                        "return_6h": 0.0,
                        "volume_ratio": 1.0,
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
            self.assertEqual(row["status"], "LIVE_EXECUTED")
            self.assertIn("Public listing signal", row["rationale"])
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
            self.assertEqual(human["crowd_regime"], "BALANCED")
            self.assertGreater(best["human_weight"], 0.0)
            self.assertNotEqual(
                best["decision_score"],
                best["pre_human_score"],
            )
            self.assertIn("Human Weather", snap["research"]["thesis"])
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


if __name__ == "__main__":
    unittest.main()
