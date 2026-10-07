import os
import unittest
from decimal import Decimal

from src.wallets.advanced_trade import AdvancedTradeSpot, AdvancedTradeVault


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class FakeClient:
    def get_accounts(self):
        return FakeResponse(
            {
                "accounts": [
                    {
                        "currency": "USDC",
                        "available_balance": {"value": "25.00", "currency": "USDC"},
                        "hold": {"value": "0", "currency": "USDC"},
                    },
                    {
                        "currency": "BTC",
                        "available_balance": {"value": "0", "currency": "BTC"},
                        "hold": {"value": "0", "currency": "BTC"},
                    },
                ]
            }
        )

    def get_products(self, **kwargs):
        return FakeResponse(
            {
                "products": [
                    {
                        "product_id": "BTC-USD",
                        "product_type": "SPOT",
                        "trading_disabled": False,
                        "cancel_only": False,
                        "limit_only": False,
                    },
                    {
                        "product_id": "ETH-USDC",
                        "product_type": "SPOT",
                        "trading_disabled": False,
                        "cancel_only": False,
                        "limit_only": False,
                    },
                    {
                        "product_id": "BAD-USD",
                        "product_type": "SPOT",
                        "trading_disabled": True,
                        "cancel_only": False,
                        "limit_only": False,
                    },
                ]
            }
        )

    def preview_market_order_buy(self, **kwargs):
        return FakeResponse({"preview_id": "p1", **kwargs})

    def market_order_buy(self, **kwargs):
        return FakeResponse({"success": True, **kwargs})

    def market_order_sell(self, **kwargs):
        return FakeResponse({"success": True, **kwargs})


class FakeVault(AdvancedTradeVault):
    def configured(self):
        return True

    def client(self, timeout=10):
        return FakeClient()


class AdvancedTradeTests(unittest.TestCase):
    def test_status_lists_balances_and_tradable_spot_products(self):
        status = AdvancedTradeSpot(FakeVault()).status()
        self.assertEqual(status.tradable_spot_products, 2)
        self.assertEqual(set(status.product_ids), {"BTC-USD", "ETH-USDC"})
        self.assertEqual(status.balances[0].currency, "USDC")
        self.assertEqual(status.balances[0].available, Decimal("25.00"))

    def test_preview_and_order_wrappers(self):
        rail = AdvancedTradeSpot(FakeVault())
        preview = rail.preview_market_buy("eth-usdc", Decimal("5"))
        self.assertEqual(preview["product_id"], "ETH-USDC")
        buy = rail.market_buy("btc-usd", Decimal("3"), "test-buy")
        self.assertTrue(buy["success"])
        sell = rail.market_sell("btc-usd", Decimal("0.001"), "test-sell")
        self.assertTrue(sell["success"])


if __name__ == "__main__":
    unittest.main()
