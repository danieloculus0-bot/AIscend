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
                    tradable_spot_products=1,
                    product_ids=("MEME-EUR",),
                )

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
            engine.step()
            row = store.latest_decisions(1)[0]
            self.assertEqual(row["action"], "BUY")
            self.assertEqual(row["symbol"], "MEME-EUR")
            self.assertEqual(row["status"], "LIVE_EXECUTED")
            self.assertTrue(rail.buys)
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
