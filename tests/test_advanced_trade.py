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
    def __init__(self):
        self.posts = []

    def get_api_key_permissions(self):
        return FakeResponse({"can_view": True, "can_trade": True, "can_transfer": True})

    def get(self, path, params=None, **kwargs):
        if path == "/v2/accounts":
            return {
                "data": [
                    {"id": "usdc-account", "currency": {"code": "USDC"}},
                ]
            }
        if path == "/v2/accounts/usdc-account/addresses":
            return {"data": []}
        raise AssertionError(path)

    def post(self, path, data=None, **kwargs):
        self.posts.append((path, data))
        if path == "/v2/accounts/usdc-account/addresses":
            return {
                "data": {
                    "address": "0x1111111111111111111111111111111111111111",
                    "network": "base",
                }
            }
        if path == "/v2/accounts/usdc-account/transactions":
            return {"data": {"id": "tx-1", "status": "pending"}}
        raise AssertionError(path)

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
                    {
                        "product_id": "EARLY-USD",
                        "product_type": "SPOT",
                        "trading_disabled": False,
                        "cancel_only": False,
                        "limit_only": True,
                        "post_only": False,
                        "auction_mode": False,
                    },
                ]
            }
        )

    def get_product(self, product_id, **kwargs):
        return FakeResponse(
            {
                "product_id": product_id.upper(),
                "price": "100",
                "base_increment": "0.00000001",
                "quote_increment": "0.01",
                "base_min_size": "0.00000001",
                "quote_min_size": "0.01",
            }
        )

    def preview_market_order_buy(self, **kwargs):
        return FakeResponse({"preview_id": "p1", **kwargs})

    def preview_market_order_sell(self, **kwargs):
        return FakeResponse({"preview_id": "p2", **kwargs})

    def market_order_buy(self, **kwargs):
        return FakeResponse(
            {
                "success": True,
                "success_response": {"order_id": "buy-1"},
                **kwargs,
            }
        )

    def market_order_sell(self, **kwargs):
        return FakeResponse(
            {
                "success": True,
                "success_response": {"order_id": "sell-1"},
                **kwargs,
            }
        )

    def get_order(self, order_id):
        return FakeResponse(
            {
                "order": {
                    "order_id": order_id,
                    "status": "FILLED",
                    "filled_size": "1",
                    "filled_value": "1",
                    "total_fees": "0.01",
                    "settled": True,
                }
            }
        )


class FakeVault(AdvancedTradeVault):
    def __init__(self):
        self.fake_client = FakeClient()

    def configured(self):
        return True

    def client(self, timeout=10):
        return self.fake_client


class AdvancedTradeTests(unittest.TestCase):
    def test_status_lists_balances_and_tradable_spot_products(self):
        status = AdvancedTradeSpot(FakeVault()).status()
        self.assertEqual(status.tradable_spot_products, 2)
        self.assertEqual(set(status.product_ids), {"BTC-USD", "ETH-USDC"})
        self.assertEqual(status.balances[0].currency, "USDC")
        self.assertEqual(status.balances[0].available, Decimal("25.00"))
        self.assertTrue(status.can_trade)
        self.assertTrue(status.can_transfer)

    def test_bridge_address_and_send_wrappers(self):
        vault = FakeVault()
        rail = AdvancedTradeSpot(vault)
        os.environ.pop("ADVANCED_USDC_BASE_ADDRESS", None)
        address = rail.receive_address("USDC", "base")
        self.assertEqual(address, "0x1111111111111111111111111111111111111111")
        sent = rail.send_usdc_to_address(
            "0x2222222222222222222222222222222222222222",
            Decimal("5"),
            network="base",
            idem="bridge-1",
        )
        self.assertEqual(sent["data"]["id"], "tx-1")
        self.assertTrue(
            any(path.endswith("/transactions") for path, _ in vault.fake_client.posts)
        )

    def test_preview_and_order_wrappers(self):
        rail = AdvancedTradeSpot(FakeVault())
        preview = rail.preview_market_buy("eth-usdc", Decimal("5"))
        self.assertEqual(preview["product_id"], "ETH-USDC")
        sell_preview = rail.preview_market_sell("btc-usd", Decimal("0.001"))
        self.assertEqual(sell_preview["product_id"], "BTC-USD")
        buy = rail.market_buy("btc-usd", Decimal("3"), "test-buy")
        self.assertTrue(buy["success"])
        sell = rail.market_sell("btc-usd", Decimal("0.001"), "test-sell")
        self.assertTrue(sell["success"])
        order = rail.order("buy-1")
        self.assertEqual(order["order"]["status"], "FILLED")


if __name__ == "__main__":
    unittest.main()
