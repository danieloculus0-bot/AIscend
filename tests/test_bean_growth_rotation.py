"""Actual learning-ranked portfolio rotation through mocked Advanced execution."""
import tempfile
import unittest
from pathlib import Path

from src.advanced_live import AdvancedSpotEngine
from src.bean_growth_rotation import evaluate_candidates
from src.core import StateStore


class LearnedPolicy:
    def __init__(self):
        self.calls = []
    def assess(self, product):
        self.calls.append(product)
        profiles = {
            "ETH-USDC": ("momentum", 1, 8),
            "ALEO-USDC": ("reversion", 1, 400),
            "DIMO-USDC": ("cash", 0, 0),
        }
        action, target, reward = profiles[product]
        return {
            "trade_effect": "ADOPTED_LONG_CASH",
            "learned_strategy": action,
            "target_exposure": target,
            "chosen_action_scores_bps": {action: reward},
            "behavior_revisions": 3,
            "reward_updates": 230,
            "forward_bars": 5,
        }


class RotationTests(unittest.TestCase):
    @staticmethod
    def snapshot(held=None, cash=None):
        return {
            "positions": held or {},
            "cash_by_quote": cash or {},
            "tradable_products": ["ETH-USDC", "ALEO-USDC", "DIMO-USDC"],
            "asset_universe": {"scored": [
                {"product": "ETH-USDC", "score": .9, "spread_bps": 5,
                 "volume_ratio": 1.5},
                {"product": "ALEO-USDC", "score": .7, "spread_bps": 4,
                 "volume_ratio": 1.2},
                {"product": "DIMO-USDC", "score": .6, "spread_bps": 7,
                 "volume_ratio": 1.0},
            ]},
        }

    @staticmethod
    def make_engine(store, learner, calls):
        engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
        engine.store = store
        engine.growth_gate = learner
        engine.sync = lambda: {"snapshot_refreshed": True}
        engine._execute_buy = lambda d, snap: calls.append(("BUY", d.symbol, d.fraction))
        engine._execute_sell = lambda d, snap: calls.append(("SELL", d.symbol, d.fraction))
        return engine

    def test_learned_market_wins_over_scanner_rank(self):
        learner = LearnedPolicy()
        result = evaluate_candidates(learner, self.snapshot(cash={"USDC": 25.0}))
        self.assertEqual(result["best_long"]["product"], "ALEO-USDC")
        # Prospective conversion from paper reward to live choice must use
        # the same 60-bps taker fallback, never the former hidden 7-bps value.
        self.assertAlmostEqual(result["best_long"]["edge_bps"], 400 - 60 - 4 / 2)
        self.assertEqual(result["assessed_count"], 3)
        self.assertEqual(set(learner.calls),
                         {"ETH-USDC", "ALEO-USDC", "DIMO-USDC"})

    def test_existing_hold_is_sold_then_proceeds_are_reinvested_next_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "account.db")
            calls = []
            engine = self.make_engine(store, LearnedPolicy(), calls)
            radar = {"best_long": {"product": "ETH-USDC", "score": .9}}
            # First cycle: capital is in a weaker ETH strategy.
            engine._adopt_growth(self.snapshot(
                held={"ETH-USDC": {"qty": .1, "available_qty": .1}}), radar)
            self.assertEqual(calls[0][:2], ("SELL", "ETH-USDC"))
            self.assertEqual(store.latest_decisions(1)[0]["action"], "SELL")
            self.assertIn("rotation", store.latest_decisions(1)[0]["rationale"])
            # Next cycle: exchange snapshot confirms released quote funds.
            engine._adopt_growth(self.snapshot(cash={"USDC": 22.0}), radar)
            self.assertEqual(calls[1][:2], ("BUY", "ALEO-USDC"))
            self.assertEqual(store.latest_decisions(1)[0]["symbol"], "ALEO-USDC")
            self.assertEqual(store.latest_decisions(1)[0]["status"], "ORDER_ACCEPTED_UNVERIFIED")
            store.close()

    def test_evaluation_rotates_beyond_first_eight_without_ignoring_holdings(self):
        class WideLearner:
            def __init__(self):
                self.calls = set()
            def assess(self, product):
                self.calls.add(product)
                reward = 100 if product == "COIN69-USDC" else 15
                return {"trade_effect": "ADOPTED_LONG_CASH",
                        "learned_strategy": "momentum",
                        "target_exposure": 1,
                        "chosen_action_scores_bps": {"momentum": reward}}

        products = [f"COIN{i:02d}-USDC" for i in range(70)]
        snapshot = {
            "positions": {"COIN50-USDC": {"qty": 1}},
            "tradable_products": products,
            "asset_universe": {"scored": [
                {"product": p, "score": 0.70 - i * 0.01,
                 "spread_bps": 2, "volume_ratio": 1.0}
                for i, p in enumerate(products)]},
        }
        learner = WideLearner()
        cursor = 0
        observed_winner = False
        for _ in range(4):
            plan = evaluate_candidates(learner, snapshot, cursor=cursor)
            cursor = plan["next_cursor"]
            self.assertIn("COIN50-USDC", learner.calls)
            self.assertEqual(plan["eligible_count"], 70)
            if plan["best_long"] and plan["best_long"]["product"] == "COIN69-USDC":
                observed_winner = True
        self.assertTrue(observed_winner)
        self.assertIn("COIN69-USDC", learner.calls)

    def test_rejected_held_position_sells_even_without_cash(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "account.db")
            calls = []
            engine = self.make_engine(store, LearnedPolicy(), calls)
            engine._adopt_growth(self.snapshot(
                held={"DIMO-USDC": {"qty": 12, "available_qty": 12}}), {})
            self.assertEqual(calls[0][:2], ("SELL", "DIMO-USDC"))
            store.close()


if __name__ == "__main__":
    unittest.main()
