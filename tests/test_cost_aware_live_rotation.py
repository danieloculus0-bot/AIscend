"""Full-wallet planning through mocked Advanced Spot execution methods."""
import tempfile
import unittest
from pathlib import Path

from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore


class PredictiveLearner:
    def assess(self, product):
        if product != "ALEO-USD":
            return {"status": "DATA_UNAVAILABLE", "trade_effect": "NONE"}
        return {"status": "READY", "trade_effect": "ADOPTED_LONG_CASH",
                "learned_strategy": "buy_hold", "target_exposure": 1,
                "chosen_action_scores_bps": {"buy_hold": 40},
                "observed_samples": 20, "observed_win_rate": .65}


class CostAwareLiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "advanced.db")
        self.actions = []
        self.engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
        self.engine.store = self.store
        self.engine.growth_gate = PredictiveLearner()
        self.engine.sync = lambda: {"refreshed": True}
        self.engine._execute_sell = lambda d, s: self.actions.append(
            ("SELL", d.symbol, d.rationale))
        self.engine._execute_buy = lambda d, s: self.actions.append(
            ("BUY", d.symbol, d.rationale))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def snap(self, *, cash=None, held=None):
        return {"positions": held or {},
                "cash_by_quote": cash or {},
                "balance_values_usd": {"GROVE": 20.0, "ALEO": 19.0},
                "prices": {"GROVE-USDC": 2.0, "ALEO-USD": 2.0},
                "tradable_products": ["ALEO-USD", "GROVE-USDC", "USDC-USD"],
                "asset_universe": {"scored": [
                    {"product": "ALEO-USD", "score": .9,
                     "spread_bps": 4.0, "return_5m": .01,
                     "return_1h": .03, "return_6h": .08,
                     "volume_ratio": 2.0},
                    {"product": "GROVE-USDC", "score": -.1,
                     "spread_bps": 8.0}]},
                }

    def test_sale_then_bridge_then_entry_then_maturity(self):
        held = {"GROVE-USDC": {"qty": 10, "available_qty": 10}}
        self.engine._adopt_growth(self.snap(
            cash={"USDC": .8827}, held=held), {})
        self.assertEqual(self.actions[-1][:2], ("SELL", "GROVE-USDC"))
        evidence = self.store.get_meta("bean_growth_latest")
        self.assertIn('"conversion_required": true', evidence)
        self.assertIn('"source_asset": "GROVE-USDC"', evidence)

        self.engine._adopt_growth(self.snap(cash={"USDC": 20}), {})
        self.assertEqual(self.actions[-1][:2], ("SELL", "USDC-USD"))

        self.engine._adopt_growth(self.snap(cash={"USD": 19}), {})
        self.assertEqual(self.actions[-1][:2], ("BUY", "ALEO-USD"))
        self.assertGreater(float(
            self.store.get_meta("bean_maturity_at:ALEO-USD")), 0)

        self.store.set_meta("bean_maturity_at:ALEO-USD", "1")
        self.engine._adopt_growth(self.snap(
            held={"ALEO-USD": {"qty": 9, "available_qty": 9}}), {})
        self.assertEqual(self.actions[-1][:2], ("SELL", "ALEO-USD"))
        self.assertIn("maturity", self.actions[-1][2])

    def test_negative_paper_edge_does_not_trigger_costly_switch(self):
        class WeakLearner(PredictiveLearner):
            def assess(self, product):
                result = super().assess(product)
                if product == "ALEO-USD":
                    result["chosen_action_scores_bps"] = {"buy_hold": -5.1}
                return result
        self.engine.growth_gate = WeakLearner()
        self.engine._adopt_growth(self.snap(cash={"USDC": .8827}), {})
        decision = self.store.latest_decisions(1)[0]
        self.assertEqual(decision["action"], "HOLD")
        self.assertIn("route costs", decision["rationale"])
        self.assertEqual(self.actions, [])


if __name__ == "__main__":
    unittest.main()
