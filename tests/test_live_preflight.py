"""No-trade Coinbase release preflight regressions."""
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from scripts.live_preflight import inspect_advanced


class Key:
    def __init__(self, configured=True):
        self.ok = configured
    def configured(self):
        return self.ok


class Spot:
    def __init__(self):
        self.calls = 0
        self.value = SimpleNamespace(
            can_view=True, can_trade=True, tradable_spot_products=1,
            product_ids=("GROVE-USDC",),
            balances=(
                SimpleNamespace(currency="GROVE",
                                available=Decimal("10"), hold=Decimal("0")),
            ),
        )
    def status(self):
        self.calls += 1
        return self.value


class ReleasePreflightTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "live-advanced.db"
        self.spot = Spot()

    def test_funded_account_with_trade_permission_passes_read_only(self):
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertTrue(report["execution_ready"])
        self.assertTrue(report["exchange_access"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["placed_orders"])
        self.assertEqual(self.spot.calls, 1)

    def test_unconfigured_key_blocks_without_contacting_exchange(self):
        report = inspect_advanced(
            vault=Key(False), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertEqual(self.spot.calls, 0)

    def test_unresolved_coinbase_order_is_explicit_release_blocker(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO meta VALUES(?,?)", (
                "pending_coinbase_order", json.dumps({
                    "product": "GROVE-USDC", "client_order_id": "aiscend-1",
                    "side": "SELL", "order_id": None})))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("UNRECONCILED_COINBASE_SUBMISSION", report["blockers"])
        self.assertEqual(report["pending_product"], "GROVE-USDC")
        self.assertNotIn("aiscend-1", json.dumps(report))

    def test_key_missing_trade_permission_blocks(self):
        self.spot.value.can_trade = False
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("COINBASE_KEY_MISSING_VIEW_OR_TRADE", report["blockers"])

    def test_inaccessible_exchange_blocks(self):
        def broken():
            raise TimeoutError("Coinbase timed out")
        self.spot.status = broken
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("COINBASE_ACCOUNT_QUERY_FAILED", report["blockers"])


if __name__ == "__main__":
    unittest.main()
