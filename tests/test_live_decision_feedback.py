import unittest

from src.position_cost_basis import rebuild_position_basis
from src.primary_strategy import exit_reason, select_plan


def fill(i, side, size, price, fee="0"):
    return {"entry_id": str(i), "trade_time": f"2026-10-01T00:00:0{i}Z",
            "side": side, "product_id": "GROVE-USDC",
            "size": str(size), "price": str(price), "commission": str(fee)}


class ActualTradeDecisionTests(unittest.TestCase):
    def test_authenticated_buy_fills_establish_loss_basis(self):
        basis = rebuild_position_basis("GROVE-USDC", 10,
            [fill(1, "BUY", 10, 2, ".2")], complete_history=True)
        self.assertEqual(basis["cost_basis_status"], "verified_coinbase_fills")
        self.assertAlmostEqual(basis["avg_cost"], 2.02)
        snap = {"asset_universe": {"scored": []}}
        self.assertEqual(exit_reason(
            "GROVE-USDC", snap, verified_entry_price=basis["avg_cost"],
            current_mark_price=1.8), "verified_entry_loss_limit")

    def test_partial_sale_reduces_basis_and_quantity(self):
        b = rebuild_position_basis("GROVE-USDC", 6,
            [fill(1,"BUY",10,2),fill(2,"SELL",4,1.5)],
            complete_history=True)
        self.assertAlmostEqual(b["avg_cost"], 2)
        self.assertEqual(b["cost_basis_status"], "verified_coinbase_fills")

    def test_unexplained_transfer_cannot_fabricate_basis(self):
        b = rebuild_position_basis("GROVE-USDC", 15,
            [fill(1,"BUY",10,2)], complete_history=True)
        self.assertIsNone(b["avg_cost"])
        self.assertEqual(b["cost_basis_status"], "coinbase_fill_balance_mismatch_or_transfer")

    def test_incomplete_pagination_unverified(self):
        b = rebuild_position_basis("GROVE-USDC", 10,
            [fill(1,"BUY",10,2)], complete_history=False)
        self.assertIsNone(b["avg_cost"])

    def test_maturing_hold_exits_with_no_scanner_row(self):
        self.assertEqual(exit_reason("GROVE-USDC",
            {"asset_universe":{"scored":[]}}, deadline_reached=True),
            "planned_maturity_exit_unobserved")

    def test_observed_price_fall_exits_without_scanner(self):
        self.assertEqual(exit_reason("GROVE-USDC",
            {"asset_universe":{"scored":[]}}, observed_peak_price=2.0,
            current_mark_price=1.82), "observed_trailing_drawdown")

    def test_adopted_q_cash_exits_instead_of_fake_hold(self):
        self.assertEqual(exit_reason("GROVE-USDC",
            {"asset_universe":{"scored":[]}}, learned_exposure=False),
            "bean_q_policy_cash")

    def test_adopted_q_long_is_executable_candidate(self):
        snap={"asset_universe":{"scored":[]},
              "positions":{},
              "cash_by_quote":{"USDC":25},
              "tradable_products":["GROVE-USDC"]}
        learned=[{"product":"GROVE-USDC","target_exposure":1,
                  "action":"momentum","reward_available":True,
                  "reward_bps":320,"spread_bps":4,
                  "return_5m":.015,"return_1h":.04,"return_6h":.08}]
        p=select_plan(snap,taker_bps=60,learned_candidates=learned)
        self.assertEqual(p["winner"]["product"],"GROVE-USDC")
        self.assertEqual(p["winner"]["strategy"],"bean_q_momentum")
        self.assertGreater(p["winner"]["net_projected_bps"],0)

    def test_no_dummy_buy_for_q_cash(self):
        snap={"asset_universe":{"scored":[]},
              "positions":{},"cash_by_quote":{"USDC":25},
              "tradable_products":["GROVE-USDC"]}
        learned=[{"product":"GROVE-USDC","target_exposure":0,
                  "action":"cash","reward_available":True,"reward_bps":400}]
        self.assertIsNone(select_plan(
            snap,taker_bps=60,learned_candidates=learned)["winner"])


class FundedExecutionPathTests(unittest.TestCase):
    """Run the actual primary trade method, with ONLY Coinbase calls mocked."""

    def test_funded_grove_is_sold_when_q_policy_selects_cash(self):
        import tempfile
        from pathlib import Path
        from src.core import StateStore
        from src.advanced_live import AdvancedSpotEngine

        class CashLearner:
            def assess(self, product):
                return {"trade_effect": "ADOPTED_LONG_CASH",
                        "learned_strategy": "cash",
                        "target_exposure": 0,
                        "chosen_action_scores_bps": {"cash": 30},
                        "observed_samples": 60}

        class Coinbase:
            def position_fills(self, product):
                return {"complete": True,
                        "fills": [fill(1, "BUY", 10, 2)]}

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "trading.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = Coinbase()
                engine.growth_gate = CashLearner()
                engine._taker_cost_bps = lambda: (60.0, "test_actual_taker")
                engine.sync = lambda: {"refreshed": True}
                submitted = []
                engine._execute_sell = lambda d, snap: (
                    submitted.append((d.action, d.symbol, d.rationale))
                    or "FILLED_VERIFIED")
                snapshot = {
                    "asset_universe": {"scored": []},
                    "tradable_products": ["GROVE-USDC"],
                    "positions": {"GROVE-USDC": {
                        "qty": 10, "available_qty": 10, "mark_price": 1.65}},
                    "cash_by_quote": {"USDC": 0.6},
                    "balance_values_usd": {"GROVE": 16.5},
                    "prices": {"GROVE-USDC": 1.65},
                }
                engine._primary_trade_step(snapshot)
                self.assertEqual(submitted[0][:2], ("SELL", "GROVE-USDC"))
                self.assertIn("bean_q_policy_cash", submitted[0][2])
                plan = __import__("json").loads(
                    store.get_meta("primary_trade_plan_latest"))
                self.assertEqual(plan["holding_reviews"]["GROVE-USDC"][
                    "cost_basis_status"], "verified_coinbase_fills")
                self.assertAlmostEqual(plan["holding_reviews"]["GROVE-USDC"][
                    "entry_price"], 2.0)
                self.assertEqual(plan["execution_status"], "FILLED_VERIFIED")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
