"""Durable authenticated fill accounting, loss feedback, no demo trades."""
import copy
from pathlib import Path
import tempfile
import unittest

from src.bean_capital_evidence import (
    capital_snapshot, evaluate_account, execution_history, preserve_fills,
)
from src.bean_growth_rotation import evaluate_candidates
from src.core import StateStore
from src.wallets.advanced_trade import AdvancedTradeSpot


def fill(idx, side, size, price, fee, *, product="GROVE-USDC",
         currency=None):
    value = {
        "entry_id": str(idx), "trade_id": str(idx),
        "trade_time": "2026-10-21T%02d:00:00Z" % idx,
        "product_id": product, "side": side, "size": str(size),
        "price": str(price), "commission": str(fee),
    }
    if currency is not None:
        value["fee_currency"] = currency
    return value


class RealReader:
    def __init__(self, fills):
        self.fills = fills
        self.calls = 0
    def account_fills(self):
        self.calls += 1
        return {"fills": self.fills, "complete": True}


class Learner:
    def assess(self, product):
        return {"trade_effect": "ADOPTED_LONG_CASH",
                "target_exposure": 1,
                "learned_strategy": "momentum",
                "chosen_action_scores_bps": {"momentum": 400.0}}


class AccountEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = StateStore(Path(self.folder.name) / "account.sqlite3")
        self.addCleanup(self.store.close)
        self.fills = [
            fill(1, "BUY", 3, 10, ".30"),
            fill(2, "SELL", 1, 8, ".10"),
            fill(3, "SELL", 1, 8, ".10"),
            fill(4, "SELL", 1, 8, ".10"),
        ]
        self.snap = {
            "starting_cash": 25.0,
            "net_liquidation": 18.4,
            "valuation_complete": True,
            "cash_by_quote": {"USDC": 18.4},
            "unpriced_balances": {},
        }

    def test_starting_bankroll_is_first_observation_not_earnings(self):
        c = capital_snapshot(self.snap)
        self.assertEqual(c["starting_equity_usd"], 25)
        self.assertEqual(c["current_equity_usd"], 18.4)
        self.assertAlmostEqual(c["equity_change_usd"], -6.6)
        self.assertFalse(c["equity_change_is_profit"])
        self.assertFalse(c["cash_flows_reconciled"])

    def test_unpriced_assets_block_misleading_total_account_equity(self):
        c = capital_snapshot({**self.snap,
                              "valuation_complete": False,
                              "unpriced_balances": {"UNKNOWN": 100}})
        self.assertIsNone(c["current_equity_usd"])
        self.assertIsNone(c["equity_change_usd"])

    def test_persist_and_replay_reconciled_net_losses_from_real_fills(self):
        payload = {"fills": self.fills, "complete": True}
        preserve_fills(self.store, payload, clock=lambda: 1000)
        preserve_fills(self.store, payload, clock=lambda: 1000)
        h = execution_history(self.store, clock=lambda: 1001)
        self.assertEqual(h["status"], "COMPLETE")
        self.assertEqual(h["fill_count_retained"], 4)
        p = h["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "VERIFIED_NET_CLOSED_TRADES")
        self.assertEqual(p["closed_sells"], 3)
        self.assertEqual(p["wins"], 0)
        self.assertAlmostEqual(p["realized_net_quote"], -6.6)
        self.assertAlmostEqual(p["matched_sold_cost_quote"], 30.3)

    def test_missing_initial_buy_cannot_fake_realized_profit(self):
        preserve_fills(self.store, {
            "fills": [fill(2, "SELL", 3, 20, ".1")], "complete": True,
        }, clock=lambda: 1000)
        p = execution_history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "UNVERIFIED")
        self.assertIsNone(p["realized_net_quote"])

    def test_unknown_or_other_fee_currency_is_unverified(self):
        preserve_fills(self.store, {"fills": [
            fill(1, "BUY", 1, 10, ".1", currency="BTC"),
            fill(2, "SELL", 1, 20, ".1")], "complete": True},
            clock=lambda: 1000)
        p = execution_history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "UNVERIFIED")
        self.assertIsNone(p["realized_net_quote"])

    def test_partial_and_stale_provider_history_cannot_count_as_complete(self):
        preserve_fills(self.store, {"fills": self.fills, "complete": True},
                       clock=lambda: 1000)
        self.assertEqual(execution_history(self.store, clock=lambda: 1001)["status"],
                         "COMPLETE")
        preserve_fills(self.store, {"fills": self.fills, "complete": False},
                       clock=lambda: 1002)
        self.assertEqual(execution_history(self.store, clock=lambda: 1003)["status"],
                         "UNVERIFIED_OR_STALE")
        preserve_fills(self.store, {"fills": self.fills, "complete": True},
                       clock=lambda: 1004)
        self.assertEqual(execution_history(self.store, clock=lambda: 3000)["status"],
                         "UNVERIFIED_OR_STALE")

    def test_conflicting_duplicate_fill_is_rejected_not_replaced(self):
        preserve_fills(self.store, {"fills": self.fills, "complete": True},
                       clock=lambda: 1000)
        changed = copy.deepcopy(self.fills)
        changed[0]["price"] = "999"
        with self.assertRaises(ValueError):
            preserve_fills(self.store, {"fills": changed, "complete": True},
                           clock=lambda: 1001)

    def test_live_account_reader_is_throttled_and_learner_sees_history(self):
        reader = RealReader(self.fills)
        first = evaluate_account(self.store, self.snap, reader,
                                 clock=lambda: 1000)
        second = evaluate_account(self.store, self.snap, reader,
                                  clock=lambda: 1001)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(first["read_status"], "PROVIDER_HISTORY_COMPLETE")
        self.assertEqual(second["read_status"], "CACHED_HISTORY")
        self.assertFalse(second["realized_account_profit_verified"])
        snap = {
            "asset_universe": {"scored": [
                {"product": "GROVE-USDC", "score": 3, "spread_bps": 5}]},
            "tradable_products": ["GROVE-USDC"],
            "positions": {},
        }
        baseline = evaluate_candidates(Learner(), snap)["evaluated"][0]
        costed = evaluate_candidates(
            Learner(), snap, account_history=second)["evaluated"][0]
        self.assertEqual(baseline["reward_bps"], 400)
        self.assertEqual(costed["paper_reward_bps"], 400)
        self.assertEqual(costed["historical_loss_penalty_bps"], 250)
        self.assertEqual(costed["reward_bps"], 150)
        self.assertEqual(costed["closed_fills_evidence_count"], 3)
        self.assertEqual(costed["real_trade_feedback_status"],
                         "VERIFIED_PRODUCT_EXECUTION")

    def test_without_provider_history_paper_reward_is_not_relabelled_real(self):
        snap = {
            "asset_universe": {"scored": [{"product": "GROVE-USDC", "score": 1}]},
            "positions": {},
        }
        history = {"execution": {
            "status": "UNVERIFIED_OR_STALE",
            "products": {"GROVE-USDC": {
                "status": "VERIFIED_NET_CLOSED_TRADES",
                "closed_sells": 300, "return_on_matched_cost_bps": -8000}},
        }}
        row = evaluate_candidates(Learner(), snap, account_history=history)["evaluated"][0]
        self.assertEqual(row["historical_loss_penalty_bps"], 0)
        self.assertEqual(row["reward_bps"], 400)
        self.assertEqual(row["real_trade_feedback_status"],
                         "UNVERIFIED_OR_INCOMPLETE")

    def test_broken_fill_reader_reports_problem_without_inventing_fills(self):
        class Broken:
            def account_fills(self):
                raise TimeoutError("offline")
        state = evaluate_account(self.store, self.snap, Broken(), clock=lambda: 1000)
        self.assertEqual(state["read_status"], "HISTORY_READ_FAILED:TimeoutError")
        self.assertFalse(state["execution"]["history_fully_reconciled"])
        self.assertEqual(state["capital"]["current_equity_usd"], 18.4)

    def test_authenticated_reader_exhausts_all_pages_including_sold_positions(self):
        class Client:
            def __init__(self):
                self.calls = []
            def get_fills(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return {"fills": [fill(1, "BUY", 1, 10, ".1")],
                            "cursor": "next"}
                return {"fills": [fill(2, "SELL", 1, 11, ".1")], "cursor": ""}
        class Vault:
            def __init__(self):
                self.instance = Client()
            def client(self):
                return self.instance
        vault = Vault()
        rail = AdvancedTradeSpot(vault=vault)
        complete = rail.account_fills()
        self.assertEqual(len(complete["fills"]), 2)
        self.assertTrue(complete["complete"])
        self.assertEqual(vault.instance.calls[0], {"limit": 100})
        self.assertEqual(vault.instance.calls[1]["cursor"], "next")
        self.assertFalse(rail.account_fills(max_pages=1)["complete"])


if __name__ == "__main__":
    unittest.main()
