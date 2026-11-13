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
                                 (90.0, "unverified_account_fee_fallback"))
                self.assertEqual(engine.rail.calls, 0)
            finally:
                store.close()


    def test_missing_authenticated_tier_uses_current_entry_fee(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "advanced.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = FakeFeeRail("e" * 64, None)
                self.assertEqual(
                    engine._taker_cost_bps(), (90.0, "conservative_estimate"))
                self.assertEqual(engine.rail.calls, 1)
            finally:
                store.close()

    def test_old_estimated_60bps_cache_cannot_underprice_new_orders(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "advanced.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = FakeFeeRail("e" * 64, None)
                store.set_meta("bean_fee_tier:" + "e" * 64,
                               '{"bps":60,"source":"conservative_estimate","checked_at":1000}')
                with patch("src.advanced_live.time.time", return_value=1001):
                    self.assertEqual(engine._taker_cost_bps()[0], 90.0)
                self.assertEqual(engine.rail.calls, 1)
            finally:
                store.close()

    def test_invalid_or_future_cache_cannot_fabricate_zero_fee(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "advanced.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = FakeFeeRail("f" * 64, None)
                for bps, checked_at in (("NaN", 1000), (-50, 1000),
                                        (25, 5000), ("invalid", 1000)):
                    with self.subTest(bps=bps, checked_at=checked_at):
                        store.set_meta("bean_fee_tier:" + "f" * 64,
                                       '{"bps":' + (
                                           '"' + bps + '"' if isinstance(bps, str)
                                           else str(bps)) +
                                       ',"source":"coinbase_account_fee_tier","checked_at":' +
                                       str(checked_at) + '}')
                        with patch("src.advanced_live.time.time", return_value=1001):
                            self.assertEqual(engine._taker_cost_bps()[0], 90.0)
            finally:
                store.close()

    def test_authenticated_discount_below_fallback_is_honored(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "advanced.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = FakeFeeRail("a" * 64, 35)
                self.assertEqual(engine._taker_cost_bps()[0], 35)
                self.assertEqual(engine._taker_cost_bps()[0], 35)
                self.assertEqual(engine.rail.calls, 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
