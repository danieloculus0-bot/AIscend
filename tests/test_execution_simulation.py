"""Regression tests for the real funded decision loop and venue lifecycle."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.execution_simulation import SimVenue, SimAudit, run_simulation, snapshot
from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore


class ExecutionSimulationTests(unittest.TestCase):
    def test_repeatable_trajectory_battery(self):
        report = run_simulation(
            seed=42, policy_cases=120, execution_cases=18)
        self.assertTrue(report["passed"], report["failures"])
        self.assertEqual(report["real_coinbase_orders"], 0)
        self.assertGreater(report["simulated_venue_orders"], 0)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.folder.name) / "sim-state.db")
        self.venue = SimVenue(price=1.6)
        self.engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
        self.engine.store = self.store
        self.engine.rail = self.venue
        self.engine.audit = SimAudit()
        self.engine.growth_gate = None
        self.engine._taker_cost_bps = lambda: (60.0, "fake-tier")
        self.engine.sync = lambda: {}
        self.store.set_meta("primary_maturity_at:GROVE-USDC",
                            str(time.time() + 3600))

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_pending_sell_is_not_resubmitted(self):
        self.venue.outcome = "OPEN"
        self.engine._primary_trade_step(snapshot(1.6))
        self.assertEqual(self.venue.submissions, 1)
        self.assertEqual(
            self.store.latest_decisions(1)[0]["status"],
            "ORDER_ACCEPTED_UNVERIFIED")
        self.assertTrue(self.store.get_meta("pending_coinbase_order"))
        self.engine._primary_trade_step(snapshot(1.5))
        self.assertEqual(self.venue.submissions, 1, "Duplicate market sell")
        self.assertEqual(
            self.store.latest_decisions(1)[0]["status"],
            "ORDER_AWAITING_VERIFICATION")

        order_id = next(iter(self.venue.orders))
        self.venue.orders[order_id]["status"] = "FILLED"
        self.engine._primary_trade_step(snapshot(1.5))
        self.assertEqual(self.venue.submissions, 1)
        self.assertEqual(
            self.store.latest_decisions(1)[0]["status"], "FILLED_VERIFIED")
        self.assertFalse(self.store.get_meta("pending_coinbase_order"))

    def test_rejected_order_clears_unaccepted_intent(self):
        self.venue.outcome = "REJECT"
        self.engine._primary_trade_step(snapshot(1.6))
        decision = self.store.latest_decisions(1)[0]
        self.assertEqual(decision["status"], "EXECUTION_ERROR")
        self.assertIn("not confirmed accepted", decision["rationale"])
        self.assertFalse(
            self.store.get_meta("pending_coinbase_order"),
            "A definitely rejected order must not block future attempts forever")

    def test_partial_terminal_fill_does_not_trigger_double_sell(self):
        self.venue.outcome = "CANCELLED"
        self.engine._primary_trade_step(snapshot(1.6))
        self.assertEqual(
            self.store.latest_decisions(1)[0]["status"],
            "PARTIAL_FILL_REQUIRES_RECONCILIATION")
        self.engine._primary_trade_step(snapshot(1.5))
        self.assertEqual(self.venue.submissions, 1)
        self.assertTrue(self.store.get_meta("pending_coinbase_order"))
        self.assertIn("PARTIAL_FILL", self.store.get_meta(
            "last_coinbase_submission_evidence"))


if __name__ == "__main__":
    unittest.main()
