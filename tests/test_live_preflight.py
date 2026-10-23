"""No-trade Coinbase release preflight regressions."""
import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

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
    def product(self, product_id):
        return {"product_id": product_id,
                "quote_min_size": "1.00", "quote_increment": "0.01",
                "base_min_size": "0.01", "base_increment": "0.00000001"}


class ReleasePreflightTests(unittest.TestCase):
    def test_cli_requires_full_history_gate_like_python_start(self):
        from scripts import live_preflight
        with patch.object(live_preflight, "inspect_advanced",
                          return_value={"execution_ready": False,
                                        "blockers": ["COINBASE_FILL_HISTORY_INCOMPLETE"]}) as inspector:
            out = StringIO()
            with redirect_stdout(out):
                code = live_preflight.main([])
            inspector.assert_called_once_with(require_history=True)
            self.assertEqual(code, 2)
            self.assertIn("COINBASE_FILL_HISTORY_INCOMPLETE", out.getvalue())

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "live-advanced.db"
        self.spot = Spot()

    def test_python_live_start_requires_complete_real_coinbase_fill_history(self):
        class HistoricalSpot(Spot):
            fills_complete = True
            def account_scope_id(self):
                return "a" * 64
            def account_fills(self):
                return {"complete": self.fills_complete,
                        "fills": [{"entry_id": "123", "product_id": "GROVE-USDC",
                                   "side": "BUY", "size": "1.2", "price": "2.5",
                                   "commission": "0.01",
                                   "trade_time": "2026-10-22T15:00:00Z"}]}
        rail = HistoricalSpot()
        report = inspect_advanced(vault=Key(), rail=rail,
                                  db_path=self.path, require_history=True)
        self.assertTrue(report["execution_ready"], report["blockers"])
        self.assertEqual(report["authenticated_fill_count"], 1)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["placed_orders"])
        rail.fills_complete = False
        blocked = inspect_advanced(vault=Key(), rail=rail,
                                   db_path=self.path, require_history=True)
        self.assertFalse(blocked["execution_ready"])
        self.assertIn("COINBASE_FILL_HISTORY_INCOMPLETE", blocked["blockers"])

    def test_python_live_start_rejects_missing_credential_identity_and_reader(self):
        result = inspect_advanced(vault=Key(), rail=self.spot,
                                  db_path=self.path, require_history=True)
        self.assertFalse(result["execution_ready"])
        self.assertIn("COINBASE_ACCOUNT_SCOPE_UNVERIFIED", result["blockers"])
        self.assertIn("COINBASE_FILL_READER_UNAVAILABLE", result["blockers"])

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

    def test_dust_balance_below_quote_minimum_is_not_execution_ready(self):
        self.spot.value.balances = (
            SimpleNamespace(currency="USD", available=Decimal("0.09"),
                            hold=Decimal("0")),)
        self.spot.value.product_ids = ("BTC-USD",)
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertEqual(report["funded_executable_pairs"], ["BTC-USD"])
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_ORDER_SIZE_ELIGIBLE_FUNDED_ROUTE", report["blockers"])

    def test_dust_holding_below_sell_minimum_is_not_execution_ready(self):
        self.spot.value.balances = (
            SimpleNamespace(currency="GROVE", available=Decimal("0.0001"),
                            hold=Decimal("0")),)
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_ORDER_SIZE_ELIGIBLE_FUNDED_ROUTE", report["blockers"])

    def test_funded_quote_with_sufficient_minimum_is_sized(self):
        self.set_funds_and_markets("USD", ("BTC-USD",))
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertTrue(report["execution_ready"], report["blockers"])
        self.assertEqual(report["sized_funded_pairs"], ["BTC-USD"])

    def test_usdc_bridge_must_clear_bridge_and_destination_minimums(self):
        self.set_funds_and_markets("USDC", ("BTC-USD", "USDC-USD"))
        self.spot.value.balances = (
            SimpleNamespace(currency="USDC", available=Decimal("0.09"),
                            hold=Decimal("0")),)
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_ORDER_SIZE_ELIGIBLE_FUNDED_ROUTE", report["blockers"])
        self.spot.value.balances = (
            SimpleNamespace(currency="USDC", available=Decimal("50"),
                            hold=Decimal("0")),)
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertTrue(report["execution_ready"], report["blockers"])
        self.assertEqual(report["sized_funded_pairs"], ["BTC-USD"])

    def test_unknown_product_rules_cannot_claim_size_verified(self):
        self.set_funds_and_markets("USD", ("BTC-USD",))
        def unavailable(_product_id):
            raise TimeoutError("product rules unavailable")
        self.spot.product = unavailable
        report = inspect_advanced(vault=Key(), rail=self.spot, db_path=self.path)
        self.assertFalse(report["execution_ready"])
        self.assertIn("NO_ORDER_SIZE_ELIGIBLE_FUNDED_ROUTE", report["blockers"])
        self.assertIn("PRODUCT_RULES_UNAVAILABLE", report["warnings"])

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
