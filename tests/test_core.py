import tempfile
import unittest
from pathlib import Path

from src.core import Decision, Engine, RiskGovernor, StateStore


class SequenceDecider:
    def __init__(self):
        self.count = 0

    def decide(self, snapshot):
        self.count += 1
        if self.count == 1:
            return Decision("BUY", "CLIP", 1.0, "test buy")
        return Decision("SELL", "CLIP", 1.0, "test sell")


class CoreTests(unittest.TestCase):
    def make_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = StateStore(Path(temp.name) / "state.db")
        store.initialize(10.0)
        self.addCleanup(store.close)
        return store

    def test_initial_bankroll(self):
        store = self.make_store()
        engine = Engine(store)
        snap = engine.snapshot()
        self.assertAlmostEqual(snap["cash"], 10.0, places=6)
        self.assertAlmostEqual(snap["net_liquidation"], 10.0, places=6)
        self.assertEqual(snap["positions"], {})

    def test_autonomous_buy_then_sell_stays_inside_bankroll(self):
        store = self.make_store()
        engine = Engine(store, SequenceDecider())

        engine.step()
        after_buy = engine.snapshot()
        self.assertGreater(after_buy["positions"]["CLIP"]["qty"], 0)
        self.assertGreaterEqual(after_buy["cash"], 0)

        engine.step()
        after_sell = engine.snapshot()
        self.assertNotIn("CLIP", after_sell["positions"])
        self.assertGreaterEqual(after_sell["cash"], 0)

    def test_governor_rejects_unknown_asset(self):
        store = self.make_store()
        engine = Engine(store)
        snap = engine.snapshot()
        decision, status = RiskGovernor.normalize(
            Decision("BUY", "NOTREAL", 1.0, "bad asset"), snap
        )
        self.assertEqual(status, "REJECTED_SYMBOL")
        self.assertEqual(decision.action, "HOLD")

    def test_reset_restores_ten_dollars(self):
        store = self.make_store()
        store.cash = 2.5
        store.reset(10.0)
        self.assertAlmostEqual(store.cash, 10.0, places=6)
        self.assertAlmostEqual(store.starting_cash, 10.0, places=6)
        self.assertEqual(store.positions(), {})


if __name__ == "__main__":
    unittest.main()
