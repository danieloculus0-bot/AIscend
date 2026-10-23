"""Durable authenticated fill accounting, loss feedback, no demo trades."""
import copy
from pathlib import Path
import tempfile
import unittest

from src.bean_capital_evidence import (
    capital_snapshot, evaluate_account, execution_history, preserve_fills,
)
from src.bean_growth_rotation import evaluate_candidates
from src.position_cost_basis import rebuild_position_basis
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


SCOPE_A = "a" * 64
SCOPE_B = "b" * 64


class RealReader:
    def __init__(self, fills):
        self.fills = fills
        self.calls = 0
    def account_scope_id(self):
        return SCOPE_A
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
    def preserve(self, store, response, **kwargs):
        return preserve_fills(store, response, account_scope=SCOPE_A, **kwargs)

    def history(self, store, **kwargs):
        return execution_history(store, account_scope=SCOPE_A, **kwargs)

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
            "authenticated_scope": SCOPE_A,
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
        self.preserve(self.store, payload, clock=lambda: 1000)
        self.preserve(self.store, payload, clock=lambda: 1000)
        h = self.history(self.store, clock=lambda: 1001)
        self.assertEqual(h["status"], "COMPLETE")
        self.assertEqual(h["fill_count_retained"], 4)
        p = h["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "VERIFIED_NET_CLOSED_TRADES")
        self.assertEqual(p["closed_sells"], 3)
        self.assertEqual(p["wins"], 0)
        self.assertAlmostEqual(p["realized_net_quote"], -6.6)
        self.assertAlmostEqual(p["matched_sold_cost_quote"], 30.3)

    def test_missing_initial_buy_cannot_fake_realized_profit(self):
        self.preserve(self.store, {
            "fills": [fill(2, "SELL", 3, 20, ".1")], "complete": True,
        }, clock=lambda: 1000)
        p = self.history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "UNVERIFIED")
        self.assertIsNone(p["realized_net_quote"])

    def test_unknown_or_other_fee_currency_is_unverified(self):
        self.preserve(self.store, {"fills": [
            fill(1, "BUY", 1, 10, ".1", currency="BTC"),
            fill(2, "SELL", 1, 20, ".1")], "complete": True},
            clock=lambda: 1000)
        p = self.history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "UNVERIFIED")
        self.assertIsNone(p["realized_net_quote"])

    def test_partial_and_stale_provider_history_cannot_count_as_complete(self):
        self.preserve(self.store, {"fills": self.fills, "complete": True},
                       clock=lambda: 1000)
        self.assertEqual(self.history(self.store, clock=lambda: 1001)["status"],
                         "COMPLETE")
        self.preserve(self.store, {"fills": self.fills, "complete": False},
                       clock=lambda: 1002)
        self.assertEqual(self.history(self.store, clock=lambda: 1003)["status"],
                         "UNVERIFIED_OR_STALE")
        self.preserve(self.store, {"fills": self.fills, "complete": True},
                       clock=lambda: 1004)
        self.assertEqual(self.history(self.store, clock=lambda: 3000)["status"],
                         "UNVERIFIED_OR_STALE")

    def test_fractional_coinbase_timestamps_do_not_reverse_buy_and_sell(self):
        # Lexicographic ordering incorrectly puts '.500Z' before 'Z'.
        buy = fill(1, "BUY", 2, 10, ".20")
        sell = fill(2, "SELL", 1, 20, ".10")
        buy["trade_time"] = "2026-10-21T12:00:00Z"
        sell["trade_time"] = "2026-10-21T12:00:00.500Z"
        self.preserve(self.store, {"fills": [sell, buy], "complete": True},
                      clock=lambda: 1000)
        p = self.history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "VERIFIED_NET_CLOSED_TRADES")
        self.assertAlmostEqual(p["realized_net_quote"], 9.8)
        basis = rebuild_position_basis(
            "GROVE-USDC", 1, [sell, buy], complete_history=True)
        self.assertEqual(basis["cost_basis_status"], "verified_coinbase_fills")
        self.assertAlmostEqual(basis["avg_cost"], 10.1)

    def test_fractional_timestamp_cannot_fabricate_pre_buy_sale_profit(self):
        sell = fill(1, "SELL", 1, 20, ".10")
        buy = fill(2, "BUY", 1, 10, ".10")
        sell["trade_time"] = "2026-10-21T12:00:00Z"
        buy["trade_time"] = "2026-10-21T12:00:00.500Z"
        self.preserve(self.store, {"fills": [buy, sell], "complete": True},
                      clock=lambda: 1000)
        p = self.history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "UNVERIFIED")
        self.assertIsNone(p["realized_net_quote"])
        basis = rebuild_position_basis(
            "GROVE-USDC", 1, [buy, sell], complete_history=True)
        self.assertEqual(basis["cost_basis_status"],
                         "sell_exceeds_recorded_buy_lots")

    def test_timezone_offsets_and_nanoseconds_preserve_actual_fill_order(self):
        buy = fill(1, "BUY", 2, 10, ".20")
        sell = fill(2, "SELL", 1, 20, ".10")
        buy["trade_time"] = "2026-10-21T12:00:00.000000001+02:00"
        sell["trade_time"] = "2026-10-21T10:00:00.000000002Z"
        self.preserve(self.store, {"fills": [sell, buy], "complete": True},
                      clock=lambda: 1000)
        p = self.history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "VERIFIED_NET_CLOSED_TRADES")
        self.assertAlmostEqual(p["realized_net_quote"], 9.8)

    def test_same_instant_opposite_sides_cannot_verify_closed_profit(self):
        buy = fill(1, "BUY", 1, 10, ".10")
        sell = fill(2, "SELL", 1, 20, ".10")
        buy["trade_time"] = "2026-10-21T12:00:00.000000000Z"
        sell["trade_time"] = "2026-10-21T12:00:00Z"
        self.preserve(self.store, {"fills": [buy, sell], "complete": True},
                      clock=lambda: 1000)
        p = self.history(self.store, clock=lambda: 1001)["products"]["GROVE-USDC"]
        self.assertEqual(p["status"], "UNVERIFIED")
        basis = rebuild_position_basis(
            "GROVE-USDC", 0, [buy, sell], complete_history=True)
        self.assertEqual(basis["cost_basis_status"],
                         "ambiguous_same_instant_coinbase_fills")

    def test_missing_commission_or_invalid_timestamp_cannot_verify_cost(self):
        missing = fill(1, "BUY", 1, 10, ".10")
        missing.pop("commission")
        self.assertEqual(rebuild_position_basis(
            "GROVE-USDC", 1, [missing], complete_history=True
        )["cost_basis_status"], "missing_coinbase_fill_commission")
        with self.assertRaises(ValueError):
            self.preserve(self.store, {"fills": [missing], "complete": True},
                          clock=lambda: 1000)
        invalid = fill(1, "BUY", 1, 10, ".10")
        invalid["trade_time"] = "not-a-coinbase-timestamp"
        with self.assertRaises(ValueError):
            self.preserve(self.store, {"fills": [invalid], "complete": True},
                          clock=lambda: 1000)

    def test_impossible_maker_rebate_cannot_create_verified_profit(self):
        buy = fill(1, "BUY", 1, 10, "-11")
        sell = fill(2, "SELL", 1, 20, "0")
        # Reject the impossible fee at ingestion, before any fill is retained.
        # The previous test expected a stored UNVERIFIED trade, contradicting
        # the fail-closed commission validation added to _normalize_fill.
        with self.assertRaisesRegex(ValueError, "commission exceeds trade notional"):
            self.preserve(self.store, {"fills": [buy, sell], "complete": True},
                          clock=lambda: 1000)
        history = self.history(self.store, clock=lambda: 1001)
        self.assertEqual(history["fill_count_retained"], 0)
        self.assertFalse(history["history_fully_reconciled"])
        self.assertEqual(history["products"], {})

    def test_impossible_sell_rebate_cannot_fabricate_closed_profit(self):
        # The BUY-side sanity check alone missed an enormous SELL rebate.
        # Legitimate small negative maker fees remain accepted.
        buy = fill(1, "BUY", 1, 10, "-0.01")
        sell = fill(2, "SELL", 1, 20, "-21")
        with self.assertRaisesRegex(ValueError, "commission exceeds"):
            self.preserve(self.store, {"fills": [buy, sell], "complete": True},
                          clock=lambda: 1000)
        self.assertEqual(self.history(self.store, clock=lambda: 1001)
                         ["fill_count_retained"], 0)

    def test_small_sell_maker_rebate_remains_valid(self):
        buy = fill(1, "BUY", 1, 10, "-0.01")
        sell = fill(2, "SELL", 1, 20, "-0.02")
        self.preserve(self.store, {"fills": [buy, sell], "complete": True},
                      clock=lambda: 1000)
        product = self.history(self.store, clock=lambda: 1001)
        product = product["products"]["GROVE-USDC"]
        self.assertEqual(product["status"], "VERIFIED_NET_CLOSED_TRADES")
        self.assertAlmostEqual(product["realized_net_quote"], 10.03)

    def test_position_basis_rejects_impossible_sell_rebate(self):
        fills = [
            fill(1, "BUY", 2, 10, "0", product="BTC-USD"),
            fill(2, "SELL", 1, 20, "-21", product="BTC-USD"),
        ]
        result = rebuild_position_basis(
            "BTC-USD", 1, fills, complete_history=True)
        self.assertEqual(result["cost_basis_status"],
                         "coinbase_fee_exceeds_notional")

    def test_coinbase_micro_quantities_preserve_exact_decimal_bytes(self):
        tiny = fill(1, "BUY", "0.00000000000000012345",
                    "12345.12345678987654321", "0.00000000000000000001")
        self.preserve(self.store, {"fills": [tiny], "complete": True},
                       clock=lambda: 1000)
        item = self.history(self.store, clock=lambda: 1001)["recent_trades"][0]
        self.assertEqual(item["size"], "1.2345E-16")
        self.assertEqual(item["price"], "12345.12345678987654321")
        self.assertEqual(item["fee"], "1E-20")

    def test_conflicting_duplicate_fill_is_rejected_not_replaced(self):
        self.preserve(self.store, {"fills": self.fills, "complete": True},
                       clock=lambda: 1000)
        changed = copy.deepcopy(self.fills)
        changed[0]["price"] = "999"
        with self.assertRaises(ValueError):
            self.preserve(self.store, {"fills": changed, "complete": True},
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

    def test_new_account_or_missing_provider_fills_invalidates_old_history(self):
        self.preserve(self.store, {"fills": self.fills, "complete": True},
                       clock=lambda: 1000)
        with self.assertRaisesRegex(ValueError, "excludes prior recorded fills"):
            self.preserve(self.store, {
                "fills": self.fills[:-1], "complete": True},
                clock=lambda: 1001)
        self.assertEqual(
            self.history(self.store, clock=lambda: 1002)["status"],
            "UNVERIFIED_OR_STALE")

    def test_account_fills_are_physically_isolated_even_with_same_fill_id(self):
        self.preserve(self.store, {"fills": self.fills, "complete": True},
                      clock=lambda: 1000)
        a = self.history(self.store, clock=lambda: 1001)
        self.assertEqual(a["fill_count_retained"], 4)
        empty = execution_history(self.store, account_scope=SCOPE_B,
                                  clock=lambda: 1001)
        self.assertEqual(empty["fill_count_retained"], 0)
        self.assertEqual(empty["status"], "UNVERIFIED_OR_STALE")
        unrelated = [fill(1,"BUY",1,100,".10",product="SOL-USDC")]
        preserve_fills(self.store, {"fills": unrelated, "complete": True},
                       account_scope=SCOPE_B, clock=lambda: 1000)
        b = execution_history(self.store, account_scope=SCOPE_B,
                              clock=lambda: 1001)
        self.assertEqual(b["fill_count_retained"], 1)
        self.assertEqual(b["recent_trades"][0]["product"],"SOL-USDC")
        self.assertEqual(self.history(self.store, clock=lambda: 1001),
                         a)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) FROM bean_account_fills").fetchone()[0], 5)

    def test_previous_unscoped_fill_history_cannot_be_reclassified_as_current(self):
        self.store.conn.execute(
            "CREATE TABLE IF NOT EXISTS bean_verified_fills("
            "fill_id TEXT PRIMARY KEY,evidence TEXT NOT NULL,observed_at REAL)")
        self.store.conn.execute(
            "INSERT INTO bean_verified_fills VALUES(?,?,?)",
            ("1", '{"legacy":true}', 999))
        self.store.set_meta("bean_fills_last_complete_at", "1000")
        history = self.history(self.store, clock=lambda: 1001)
        self.assertEqual(history["fill_count_retained"], 0)
        self.assertEqual(history["status"],"UNVERIFIED_OR_STALE")

    def test_credential_switch_rejects_old_key_scheduled_evidence(self):
        self.preserve(self.store, {"fills": self.fills, "complete": True},
                      clock=lambda: 1000)
        class SwitchedReader:
            def account_scope_id(self):
                return SCOPE_B
            def account_fills(self):
                return {"fills": [], "complete": True}
        switched = evaluate_account(
            self.store, {**self.snap, "authenticated_scope": SCOPE_B},
            SwitchedReader(), clock=lambda: 1001)
        self.assertEqual(switched["execution"]["fill_count_retained"], 0)
        self.assertTrue(switched["account_scope_verified"])
        self.assertEqual(self.history(self.store, clock=lambda: 1002)["fill_count_retained"], 4)
        mismatch = evaluate_account(self.store, self.snap,
                                    SwitchedReader(), clock=lambda: 1002)
        self.assertEqual(mismatch["execution"]["status"],"UNVERIFIED_ACCOUNT_SCOPE")
        self.assertIsNone(mismatch["capital"]["starting_equity_usd"])
        self.assertFalse(mismatch["account_scope_verified"])

    def test_missing_credential_scope_cannot_resume_old_fills(self):
        self.preserve(self.store, {"fills": self.fills, "complete": True},
                      clock=lambda: 1000)
        class Disconnected:
            def account_fills(self):
                raise AssertionError("Must not read without account scope")
        report=evaluate_account(self.store,self.snap,Disconnected(),
                                clock=lambda:1001)
        self.assertEqual(report["execution"]["status"],"UNVERIFIED_ACCOUNT_SCOPE")
        self.assertEqual(report["execution"]["fill_count_retained"],0)

    def test_broken_fill_reader_reports_problem_without_inventing_fills(self):
        class Broken:
            def account_scope_id(self):
                return SCOPE_A
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
                if not kwargs.get("cursor"):
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
        self.assertEqual(vault.instance.calls[0],
                         {"limit": 100, "product_types": ["SPOT"]})
        self.assertEqual(vault.instance.calls[1]["cursor"], "next")
        self.assertFalse(rail.account_fills(max_pages=1)["complete"])


    def test_verified_position_basis_accepts_signed_quote_maker_rebates(self):
        fills = [fill(1, "BUY", 1, 100, "-0.01", product="BTC-USD")]
        result = rebuild_position_basis("BTC-USD", 1, fills, complete_history=True)
        self.assertEqual(result["cost_basis_status"], "verified_coinbase_fills")
        self.assertAlmostEqual(result["avg_cost"], 99.99)

    def test_position_basis_rejects_invalid_rebate_exceeding_lot_cost(self):
        fills = [fill(1, "BUY", 1, 100, "-101", product="BTC-USD")]
        result = rebuild_position_basis("BTC-USD", 1, fills, complete_history=True)
        self.assertEqual(result["cost_basis_status"], "coinbase_fee_exceeds_notional")

    def test_position_basis_rejects_commissions_in_other_currency(self):
        fills = [fill(1, "BUY", 1, 100, "1", product="BTC-USD",
                      currency="BTC")]
        result = rebuild_position_basis("BTC-USD", 1, fills, complete_history=True)
        self.assertEqual(result["cost_basis_status"], "mismatched_coinbase_fee_currency")


if __name__ == "__main__":
    unittest.main()
