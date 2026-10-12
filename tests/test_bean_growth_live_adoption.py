"""Mock wallet integration: learned actions actually reach AIscend live decision paths."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.core import Decision, StateStore
from src.live import LiveEngine
from src.advanced_live import AdvancedSpotEngine
from src.wallets.cdp_wallet import SwapExecution


class FakeLearner:
    def __init__(self, exposure):
        self.exposure = exposure
        self.calls = []
    def assess(self, product):
        self.calls.append(product)
        return {"status": "READY", "trade_effect": "ADOPTED_LONG_CASH",
                "target_exposure": self.exposure, "learned_strategy": "momentum"}


class HoldDecider:
    def decide(self, snapshot):
        return Decision("HOLD", None, 0, "baseline hold")


class FakeWallet:
    def __init__(self):
        self.buys = []
        self.sells = []
    async def swap_usdc_to_weth(self, fraction):
        self.buys.append(fraction)
        return SwapExecution(user_op_hash="fake-buy", from_symbol="USDC",
                             from_amount=Decimal("10"), to_symbol="WETH",
                             to_amount=Decimal("0.005"))
    async def swap_weth_to_usdc(self, fraction):
        self.sells.append(fraction)
        return SwapExecution(user_op_hash="fake-sell", from_symbol="WETH",
                             from_amount=Decimal("0.005"), to_symbol="USDC",
                             to_amount=Decimal("10"))


class LiveGrowthAdoptionTests(unittest.TestCase):
    def make_store(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = StateStore(Path(folder.name) / "wallet.sqlite3")
        self.addCleanup(store.close)
        return store

    def test_base_learner_can_initiate_buy_from_previous_hold(self):
        store = self.make_store()
        wallet = FakeWallet()
        learner = FakeLearner(exposure=1)
        engine = LiveEngine(store=store, wallet=wallet, decider=HoldDecider(),
                            growth_gate=learner)
        engine.sync = lambda: {"cash": 10, "positions": {}, "prices": {"WETH": 2000}}
        engine._record_execution = lambda *_: None
        engine.step()
        self.assertEqual(wallet.buys, [1.0])
        self.assertEqual(store.latest_decisions(1)[0]["action"], "BUY")
        self.assertEqual(store.latest_decisions(1)[0]["status"], "LIVE_EXECUTED")
        self.assertEqual(learner.calls, ["WETH"])

    def test_base_learner_sells_when_cash_policy_adopted(self):
        store = self.make_store()
        wallet = FakeWallet()
        learner = FakeLearner(exposure=0)
        engine = LiveEngine(store=store, wallet=wallet, decider=HoldDecider(),
                            growth_gate=learner)
        engine.sync = lambda: {"cash": 0, "positions": {"WETH": {"qty": .005}},
                               "prices": {"WETH": 2000}}
        engine._record_execution = lambda *_: None
        engine.step()
        self.assertEqual(wallet.sells, [1.0])
        self.assertEqual(store.latest_decisions(1)[0]["action"], "SELL")

    def test_advanced_learner_executes_mock_buy(self):
        store = self.make_store()
        engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
        engine.store = store
        engine.growth_gate = FakeLearner(exposure=1)
        engine.sync = lambda: {"mock_snapshot": True}
        submitted = []
        engine._execute_buy = lambda decision, snapshot: submitted.append(decision)
        radar = {"weakest_held": None, "best_long": {
            "product": "ETH-USDC", "decision_score": .44,
            "spread_bps": 4, "volume_ratio": 1.4}}
        snapshot = {"positions": {}, "cash_by_quote": {"USDC": 25.0}}
        result = engine._adopt_growth(snapshot, radar)
        self.assertTrue(result["mock_snapshot"])
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].action, "BUY")
        self.assertEqual(submitted[0].symbol, "ETH-USDC")
        self.assertEqual(store.latest_decisions(1)[0]["status"], "LIVE_EXECUTED")

    def test_advanced_learner_executes_mock_sell(self):
        store = self.make_store()
        engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
        engine.store = store
        engine.growth_gate = FakeLearner(exposure=0)
        engine.sync = lambda: {"mock_snapshot": True}
        submitted = []
        engine._execute_sell = lambda decision, snapshot: submitted.append(decision)
        snapshot = {"positions": {"ETH-USDC": {"qty": 0.1}},
                    "cash_by_quote": {"USDC": 0}}
        radar = {"weakest_held": {"product": "ETH-USDC"}, "best_long": None}
        engine._adopt_growth(snapshot, radar)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].action, "SELL")
        self.assertEqual(store.latest_decisions(1)[0]["status"], "LIVE_EXECUTED")


if __name__ == "__main__":
    unittest.main()
