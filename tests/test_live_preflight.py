"""No-trade Coinbase release preflight regressions."""
import json
from contextlib import closing
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

    def set_funds_and_markets(self, asset, product_ids):
        self.spot.value.balances = (
            SimpleNamespace(currency=asset, available=Decimal("50"), hold=Decimal("0")),
        )
        self.spot.value.product_ids = tuple(product_ids)
        self.spot.value.tradable_spot_products = len(product_ids)

    def test_usdc_stranded_without_usd_conversion_pair(self):
        self.set_funds_and_markets("USDC", ("BTC-USD",))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_FUNDED_EXECUTABLE_SPOT_ROUTE", report["blockers"])
        self.assertEqual(report["funded_executable_pairs"], [])
        self.assertFalse(report["placed_orders"])

    def test_usd_stranded_without_usdc_conversion_pair(self):
        self.set_funds_and_markets("USD", ("GROVE-USDC",))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_FUNDED_EXECUTABLE_SPOT_ROUTE", report["blockers"])

    def test_usdc_bridge_reaches_usd_market(self):
        self.set_funds_and_markets("USDC", ("BTC-USD", "USDC-USD"))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertTrue(report["execution_ready"], report["blockers"])
        self.assertEqual(report["funded_executable_pairs"], ["BTC-USD"])

    def test_usd_bridge_reaches_usdc_market(self):
        self.set_funds_and_markets("USD", ("GROVE-USDC", "USDC-USD"))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertTrue(report["execution_ready"], report["blockers"])
        self.assertEqual(report["funded_executable_pairs"], ["GROVE-USDC"])

    def test_direct_quote_funding_is_executable_without_bridge(self):
        self.set_funds_and_markets("USD", ("BTC-USD",))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertTrue(report["execution_ready"], report["blockers"])
        self.assertEqual(report["funded_executable_pairs"], ["BTC-USD"])

    def test_conversion_pair_alone_is_not_an_investable_spot_route(self):
        self.set_funds_and_markets("USD", ("USDC-USD",))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_FUNDED_EXECUTABLE_SPOT_ROUTE", report["blockers"])

    def test_unrelated_held_asset_cannot_satisfy_funding_check(self):
        self.set_funds_and_markets("GROVE", ("BTC-USD",))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_FUNDED_EXECUTABLE_SPOT_ROUTE", report["blockers"])

    def test_read_only_preflight_releases_existing_database_file(self):
        # SQLite's connection context alone does NOT close the handle.
        # Windows must be able to unlink the DB as soon as inspect returns.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertTrue(report["execution_ready"], report["blockers"])
        self.path.unlink()
        self.assertFalse(self.path.exists())

    def test_unconfigured_key_blocks_without_contacting_exchange(self):
        report = inspect_advanced(
            vault=Key(False), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertEqual(self.spot.calls, 0)

    def test_unresolved_coinbase_order_is_explicit_release_blocker(self):
        with closing(sqlite3.connect(self.path)) as db, db:
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
