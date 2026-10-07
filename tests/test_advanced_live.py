import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.advanced_live import AdvancedSpotEngine
from src.audit import AuditTrail
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


class FakeUniverse:
    def collect(self):
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


class FailingAudit:
    @staticmethod
    def provider_id(payload, *keys):
        return ""

    def record_trade(self, **kwargs):
        raise OSError("disk full")


class AdvancedLiveTests(unittest.TestCase):
    def test_engine_selects_best_tradable_coinbase_spot_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = FakeRail()
            audit_root = Path(td) / "audit"
            engine = AdvancedSpotEngine(
                store,
                rail=rail,
                asset_universe=FakeUniverse(),
                audit=AuditTrail(audit_root),
            )
            snap = engine.step()
            rows = store.latest_decisions(1)
            self.assertEqual(rows[0]["action"], "BUY")
            self.assertEqual(rows[0]["symbol"], "ALEO-USDC")
            self.assertEqual(rows[0]["status"], "LIVE_EXECUTED")
            self.assertTrue(rail.buys)
            self.assertLess(snap["cash"], 25.0)
            audit_text = next(audit_root.glob("aiscend-audit-*.jsonl")).read_text(
                encoding="utf-8"
            )
            self.assertIn('"event_type":"trade"', audit_text)
            self.assertIn('"rail":"coinbase-advanced"', audit_text)
            store.close()

    def test_audit_failure_does_not_replay_completed_order(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "advanced.db")
            rail = FakeRail()
            engine = AdvancedSpotEngine(
                store,
                rail=rail,
                asset_universe=FakeUniverse(),
                audit=FailingAudit(),
            )

            engine.step()

            self.assertEqual(len(rail.buys), 1)
            decision = store.latest_decisions(1)[0]
            self.assertEqual(decision["status"], "LIVE_EXECUTED")
            ledger_kinds = [row["kind"] for row in store.latest_ledger(5)]
            self.assertIn("ADVANCED_BUY", ledger_kinds)
            self.assertIn("AUDIT_ERROR", ledger_kinds)
            store.close()


if __name__ == "__main__":
    unittest.main()
