"""Authenticated Coinbase trading-fee inputs must be scoped to the active key."""
import tempfile
import unittest
from pathlib import Path

from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore


class FakeFeeRail:
    def __init__(self, scope, rate):
        self.scope = scope
        self.rate = rate
        self.calls = 0
    def account_scope_id(self):
        return self.scope
    def taker_fee_bps(self):
        self.calls += 1
        return self.rate


class FeeScopeTests(unittest.TestCase):
    def test_different_coinbase_keys_cannot_reuse_another_keys_discount(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "advanced.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                store.set_meta("bean_fee_tier", '{"bps":1,"checked_at":9999999999999}')
                a = FakeFeeRail("a"*64, 35)
                b = FakeFeeRail("b"*64, 170)
                engine.rail = a
                self.assertEqual(engine._taker_cost_bps()[0], 35)
                engine.rail = b
                self.assertEqual(engine._taker_cost_bps()[0], 170)
                engine.rail = a
                self.assertEqual(engine._taker_cost_bps()[0], 35)
                self.assertEqual(a.calls, 1)
                self.assertEqual(b.calls, 1)
                self.assertIsNone(store.get_meta("bean_fee_tier:" + "c"*64))
            finally:
                store.close()

    def test_missing_account_scope_never_uses_previous_authenticated_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "advanced.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = FakeFeeRail(None, 0)
                self.assertEqual(engine._taker_cost_bps(),
                                 (60.0, "unverified_account_fee_fallback"))
                self.assertEqual(engine.rail.calls, 0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
