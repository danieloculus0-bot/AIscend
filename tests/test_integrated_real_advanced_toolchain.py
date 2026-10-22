"""End-to-end Python execution-contract test, completely offline.

Uses the production Coinbase AdvancedTradeSpot adapter and actual engine,
funding/quote router, sizing, previews, order receipts and SQLite journal.
Only the Coinbase SDK's network client is replaced with a deterministic fake.
No credentials and no Coinbase orders are used.
"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.advanced_live import AdvancedSpotEngine
from src.audit import AuditTrail
from src.core import StateStore
from src.portfolio import choose_execution_rail
from src.wallets.advanced_trade import AdvancedTradeSpot


class FakeCoinbaseClient:
    def __init__(self):
        self.submissions = []
        self.orders = {}

    def get_product(self, **kwargs):
        return {"product_id": kwargs["product_id"],
                "quote_increment": "0.01", "quote_min_size": "1.00",
                "base_increment": "0.00000001",
                "base_min_size": "0.000001"}

    def preview_market_order_sell(self, **kwargs):
        return {"preview_id": "sell-preview", "errs": []}

    def preview_market_order_buy(self, **kwargs):
        return {"preview_id": "buy-preview", "errs": []}

    def _submit(self, side, kwargs):
        number = len(self.submissions) + 1
        order_id = "fake-coinbase-order-" + str(number)
        self.submissions.append((side, dict(kwargs)))
        size = kwargs.get("base_size", "0.002")
        value = kwargs.get("quote_size", "20")
        self.orders[order_id] = {
            "order_id": order_id, "status": "FILLED",
            "filled_size": str(size), "filled_value": str(value),
            "average_filled_price": "100",
            "total_fees": "0.12",
        }
        return {"success": True,
                "success_response": {"order_id": order_id}}

    def market_order_sell(self, **kwargs):
        return self._submit("SELL", kwargs)

    def market_order_buy(self, **kwargs):
        return self._submit("BUY", kwargs)

    def get_order(self, **kwargs):
        return {"order": self.orders[kwargs["order_id"]]}

    def get_fills(self, **kwargs):
        return {"fills": []}


class FakeVault:
    def __init__(self, client):
        self.sdk = client

    def client(self, timeout=10):
        return self.sdk


class CaptureAudit:
    provider_id = staticmethod(AuditTrail.provider_id)

    def __init__(self):
        self.trades = []

    def record_trade(self, **payload):
        self.trades.append(payload)


def candidate():
    return {"product": "BTC-USD", "return_5m": 0.012,
            "return_1h": 0.04, "return_6h": 0.08,
            "volume_ratio": 2.0, "spread_bps": 5.0}


def snapshot(cash):
    return {"cash_by_quote": cash,
            "net_liquidation": sum(cash.values()),
            "asset_universe": {"scored": [candidate()]},
            "tradable_products": ["BTC-USD", "USDC-USD"],
            "positions": {}, "balance_values_usd": {},
            "prices": {"BTC-USD": 100.0}}


class RealToolchainBoundaryTests(unittest.TestCase):
    def test_auto_rail_converts_real_quote_and_then_buys_via_sdk_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "trade.db")
            try:
                client = FakeCoinbaseClient()
                rail = AdvancedTradeSpot(vault=FakeVault(client))
                audit = CaptureAudit()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = rail
                engine.audit = audit
                engine.growth_gate = None
                engine._taker_cost_bps = lambda: (60.0, "test_fee")
                current = {"snapshot": snapshot({"USDC": 20.0})}
                engine.sync = lambda: current["snapshot"]

                advanced = {**current["snapshot"],
                            "market_radar": {"best_long": {
                                "product": "BTC-USD",
                                "decision_score": 0.5}}}
                base = {"cash": 0.0, "net_liquidation": 30.0,
                        "positions": {}, "research": {"available": False}}
                self.assertEqual(choose_execution_rail(base, advanced),
                                 "advanced")

                engine._primary_trade_step(current["snapshot"])
                first = dict(store.latest_decisions(1)[0])
                self.assertEqual((first["action"], first["symbol"],
                                  first["status"]),
                                 ("SELL", "USDC-USD", "FILLED_VERIFIED"))
                self.assertEqual(client.submissions[0][0], "SELL")
                self.assertEqual(
                    client.submissions[0][1]["product_id"], "USDC-USD")
                self.assertFalse(store.get_meta("pending_coinbase_order"))

                # Next authenticated sync has observed the conversion proceeds.
                # There is no same-cycle fake cash credit.
                current["snapshot"] = snapshot({"USD": 19.0})
                engine._primary_trade_step(current["snapshot"])
                second = dict(store.latest_decisions(1)[0])
                self.assertEqual((second["action"], second["symbol"],
                                  second["status"]),
                                 ("BUY", "BTC-USD", "FILLED_VERIFIED"))
                self.assertEqual(client.submissions[1][0], "BUY")
                self.assertEqual(
                    client.submissions[1][1]["product_id"], "BTC-USD")
                self.assertTrue(
                    str(client.submissions[1][1]["client_order_id"])
                    .startswith("aiscend-"))
                self.assertFalse(store.get_meta("pending_coinbase_order"))
                self.assertEqual(len(audit.trades), 2)
                self.assertTrue(all(
                    item["status"] == "filled_verified"
                    for item in audit.trades))
                self.assertTrue(all(
                    Decimal(str(item["fee_quantity"])) > 0
                    for item in audit.trades))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
