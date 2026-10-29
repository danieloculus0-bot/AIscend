import json
import tempfile
import time
import unittest
from pathlib import Path

from src.coinbase_order_evidence import accepted_order_id, verified_order
from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore, Decision


class OrderEvidenceTests(unittest.TestCase):
    def test_nested_coinbase_submission_id(self):
        response = {"success": True,
                    "success_response": {"order_id": "cb-order-123"}}
        self.assertEqual(accepted_order_id(response), "cb-order-123")
        self.assertIsNone(accepted_order_id({"success":True}))

    def test_immediate_verified_fill_resets_scoped_position_lifecycle(self):
        from src.advanced_live import _position_meta_key

        class Rail:
            def account_scope_id(self):
                return "a" * 64

            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10"}}

        scope = "a" * 64
        for side in ("BUY", "SELL"):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as td:
                store = StateStore(Path(td) / "immediate-fill.db")
                try:
                    engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                    engine.store, engine.rail = store, Rail()
                    old = {
                        "primary_peak:": "900",
                        "primary_fill_basis:": '{"avg_cost": 900}',
                        "primary_fill_basis_checked_at:": "123",
                        "primary_maturity_at:": "456",
                    }
                    for prefix, value in old.items():
                        store.set_meta(
                            _position_meta_key(prefix, "BTC-USD", scope), value)
                    engine._record_order_intent(
                        product="BTC-USD", side=side, client_id="client-1",
                        account_scope=scope)
                    state, _ = engine._check_or_track_submission(
                        {"success": True,
                         "success_response": {"order_id": "venue-1"}},
                        product="BTC-USD", side=side, client_id="client-1",
                        account_scope=scope)
                    self.assertEqual(state, "FILLED_VERIFIED")
                    self.assertEqual(store.get_meta("pending_coinbase_order"), "")
                    for prefix in ("primary_peak:", "primary_fill_basis:",
                                   "primary_fill_basis_checked_at:"):
                        self.assertEqual(store.get_meta(
                            _position_meta_key(prefix, "BTC-USD", scope)), "")
                    self.assertEqual(store.get_meta(_position_meta_key(
                        "primary_maturity_at:", "BTC-USD", scope)), "0")
                finally:
                    store.close()

    def test_delayed_verified_buy_restores_selected_horizon(self):
        from src.advanced_live import _position_meta_key

        class Rail:
            def account_scope_id(self):
                return "a" * 64

            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10"}}

        class Audit:
            def record_event(self, **kwargs):
                return None

        scope = "a" * 64
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "delayed-buy-maturity.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail, engine.audit = store, Rail(), Audit()
                maturity_key = _position_meta_key(
                    "primary_maturity_at:", "BTC-USD", scope)
                store.set_meta(maturity_key, "123")  # Expired while awaiting fill.
                receipt = {"order_id": "venue-1",
                           "client_order_id": "client-1",
                           "product": "BTC-USD", "side": "BUY",
                           "account_scope": scope, "maturity_minutes": 90}
                store.set_meta("pending_coinbase_order", json.dumps(receipt))
                before = time.time()
                decision, status = engine._pending_order_decision(scope=scope)
                self.assertEqual((decision.action, status),
                                 ("HOLD", "FILLED_VERIFIED"))
                self.assertEqual(store.get_meta("pending_coinbase_order"), "")
                self.assertGreaterEqual(float(store.get_meta(maturity_key)), before + 90 * 60)
                self.assertLess(float(store.get_meta(maturity_key)), before + 90 * 60 + 10)
            finally:
                store.close()

    def test_unverified_buy_preserves_frozen_maturity_until_fill(self):
        from src.advanced_live import _position_meta_key

        class Rail:
            status = "OPEN"

            def account_scope_id(self):
                return "a" * 64

            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": self.status,
                                  "filled_size": "0.1" if self.status == "FILLED" else "0",
                                  "filled_value": "10" if self.status == "FILLED" else "0"}}

        class Audit:
            def record_event(self, **kwargs):
                return None

        scope = "a" * 64
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "frozen-maturity.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail, engine.audit = store, rail, Audit()
                key = _position_meta_key("primary_maturity_at:", "BTC-USD", scope)
                engine._record_order_intent(
                    product="BTC-USD", side="BUY", client_id="client-1",
                    account_scope=scope, maturity_minutes=120)
                state, _ = engine._check_or_track_submission(
                    {"success": True, "success_response": {"order_id": "venue-1"}},
                    product="BTC-USD", side="BUY", client_id="client-1",
                    account_scope=scope)
                self.assertEqual(state, "ORDER_ACCEPTED_UNVERIFIED")
                self.assertIsNone(store.get_meta(key))
                receipt = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(receipt["maturity_minutes"], 120)
                rail.status = "FILLED"
                before = time.time()
                decision, state = engine._pending_order_decision(scope=scope)
                self.assertEqual((decision.action, state), ("HOLD", "FILLED_VERIFIED"))
                self.assertEqual(store.get_meta("pending_coinbase_order"), "")
                self.assertGreaterEqual(float(store.get_meta(key)), before + 120 * 60)
                self.assertLess(float(store.get_meta(key)), before + 120 * 60 + 10)
            finally:
                store.close()

    def test_failed_fill_metadata_reset_rolls_back_receipt_clear(self):
        import sqlite3
        from src.advanced_live import _position_meta_key

        class Rail:
            def account_scope_id(self):
                return "a" * 64

            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10"}}

        scope = "a" * 64
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "atomic-fill.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail()
                peak_key = _position_meta_key("primary_peak:", "BTC-USD", scope)
                store.set_meta(peak_key, "900")
                engine._record_order_intent(
                    product="BTC-USD", side="BUY", client_id="client-1",
                    account_scope=scope)
                original = store.get_meta("pending_coinbase_order")
                with store.conn:
                    store.conn.execute(
                        "CREATE TRIGGER reject_peak_clear BEFORE UPDATE ON meta "
                        "WHEN OLD.key='" + peak_key + "' AND NEW.value='' "
                        "BEGIN SELECT RAISE(ABORT, 'simulated reset failure'); END")
                with self.assertRaises(sqlite3.IntegrityError):
                    engine._check_or_track_submission(
                        {"success": True,
                         "success_response": {"order_id": "venue-1"}},
                        product="BTC-USD", side="BUY", client_id="client-1",
                        account_scope=scope)
                self.assertEqual(store.get_meta("pending_coinbase_order"), original)
                self.assertEqual(store.get_meta(peak_key), "900")
            finally:
                store.close()

    def test_submission_does_not_mean_fill(self):
        detail={"order":{"order_id":"123","status":"OPEN",
                         "filled_size":"0"}}
        self.assertEqual(verified_order(detail,"123")["state"],
                         "ORDER_ACCEPTED_UNVERIFIED")
        self.assertEqual(verified_order({}, "123")["state"],
                         "ORDER_ACCEPTED_UNVERIFIED")

    def test_full_fill_requires_quantity_and_venue_status(self):
        detail={"order":{"order_id":"123","status":"FILLED",
                         "filled_size":"0.001","filled_value":"9.95",
                         "total_fees":"0.05"}}
        result=verified_order(detail,"123")
        self.assertEqual(result["state"],"FILLED_VERIFIED")
        self.assertEqual(result["total_fees"],"0.05")
        detail["order"]["filled_size"]="0"
        self.assertNotEqual(verified_order(detail,"123")["state"],
                            "FILLED_VERIFIED")
        detail["order"]["filled_size"]="0.001"
        detail["order"]["order_id"]="different"
        self.assertEqual(verified_order(detail,"123")["state"],"UNVERIFIED")

    def test_cancelled_without_filled_quantity_cannot_confirm_zero_fill(self):
        evidence = {"order": {"order_id": "123", "status": "CANCELLED"}}
        result = verified_order(evidence, "123")
        self.assertEqual(result["state"], "TERMINAL_FILL_UNVERIFIED")

    def test_cancelled_with_malformed_filled_quantity_keeps_order_pending(self):
        evidence = {"order": {"order_id": "123", "status": "CANCELLED",
                              "filled_size": "not-a-number"}}
        self.assertEqual(verified_order(evidence, "123")["state"],
                         "TERMINAL_FILL_UNVERIFIED")

    def test_cancelled_zero_size_but_positive_filled_value_is_ambiguous(self):
        evidence = {"order": {"order_id": "123", "status": "CANCELLED",
                              "filled_size": "0", "filled_value": "2.34"}}
        self.assertEqual(verified_order(evidence, "123")["state"],
                         "TERMINAL_FILL_UNVERIFIED")

    def test_invalid_terminal_receipt_does_not_release_order_idempotency(self):
        class Rail:
            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": "CANCELLED"}}
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "pending.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail()
                store.set_meta("pending_coinbase_order",
                    '{"order_id":"test-123","client_order_id":"client-123",'
                    '"product":"BTC-USD","side":"BUY"}')
                decision, status = engine._pending_order_decision()
                self.assertEqual(status, "ORDER_AWAITING_VERIFICATION")
                self.assertEqual(decision.action, "HOLD")
                self.assertIn("client-123", store.get_meta(
                    "pending_coinbase_order"))
            finally:
                store.close()

    def test_pending_coinbase_order_blocks_new_orders_until_fill_verified(self):
        class Rail:
            def __init__(self):
                self.state="OPEN"
            def order_detail(self,order_id):
                return {"order":{"order_id":order_id,"status":self.state,
                                 "filled_size":"0.05" if self.state=="FILLED" else "0",
                                 "filled_value":"5.00" if self.state=="FILLED" else "0"}}
        with tempfile.TemporaryDirectory() as td:
            store=StateStore(Path(td)/"evidence.db")
            try:
                engine=AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store=store
                engine.rail=Rail()
                status, proof=engine._check_or_track_submission({
                    "success":True,"success_response":{"order_id":"order-123"}},
                    product="ALEO-USD",side="BUY",client_id="client-1")
                self.assertEqual(status,"ORDER_ACCEPTED_UNVERIFIED")
                decision,state=engine._pending_order_decision()
                self.assertEqual(state,"ORDER_AWAITING_VERIFICATION")
                self.assertEqual(decision.action,"HOLD")
                engine.rail.state="FILLED"
                decision,state=engine._pending_order_decision()
                self.assertEqual(state,"FILLED_VERIFIED")
                self.assertEqual(store.get_meta("pending_coinbase_order"),"")
            finally:
                store.close()

    def test_second_order_intent_cannot_replace_unresolved_client_id(self):
        class Rail:
            def order_detail(self, order_id):
                raise AssertionError("Unexpected authenticated lookup")
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "two-orders.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail()
                engine._record_order_intent(
                    product="BTC-USD", side="BUY", client_id="first-id",
                    account_scope="a" * 64)
                original = store.get_meta("pending_coinbase_order")
                with self.assertRaisesRegex(RuntimeError, "Unresolved Coinbase order"):
                    engine._record_order_intent(
                        product="ETH-USD", side="BUY", client_id="second-id",
                        account_scope="a" * 64)
                self.assertEqual(store.get_meta("pending_coinbase_order"), original)
                self.assertNotIn("second-id", original)
            finally:
                store.close()

    def test_two_desktop_connections_cannot_both_claim_one_pending_order(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        class Rail:
            def order_detail(self, order_id):
                raise AssertionError("No order should be queried")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shared-desktop.db"
            initializer = StateStore(path)
            initializer.close()
            barrier = Barrier(2)
            def worker(client_id):
                store = StateStore(path)
                try:
                    engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                    engine.store, engine.rail = store, Rail()
                    barrier.wait(timeout=10)
                    try:
                        engine._record_order_intent(
                            product="BTC-USD", side="BUY", client_id=client_id,
                            account_scope="a" * 64)
                        return True
                    except RuntimeError as exc:
                        self.assertIn("Unresolved Coinbase order", str(exc))
                        return False
                finally:
                    store.close()
            with ThreadPoolExecutor(max_workers=2) as executor:
                result = list(executor.map(worker, ("client-one", "client-two")))
            self.assertEqual(sorted(result), [False, True])
            reopened = StateStore(path)
            try:
                receipt = json.loads(reopened.get_meta("pending_coinbase_order"))
                self.assertIn(receipt["client_order_id"],
                              ("client-one", "client-two"))
            finally:
                reopened.close()

    def test_legacy_unscoped_pending_cannot_be_reconciled_using_new_key(self):
        class Rail:
            def __init__(self):
                self.lookups = []
            def account_scope_id(self):
                return "b" * 64
            def order_detail(self, order_id):
                self.lookups.append(order_id)
                raise AssertionError("Wrong-account lookup")
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "legacy-order.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail()
                old = json.dumps({
                    "product": "BTC-USD", "side": "SELL", "order_id": "old-123",
                    "client_order_id": "old-client"})
                store.set_meta("pending_coinbase_order", old)
                decision, status = engine._pending_order_decision(scope="b" * 64)
                self.assertEqual((decision.action, status),
                                 ("HOLD", "ORDER_ACCOUNT_SCOPE_UNKNOWN"))
                self.assertEqual(engine.rail.lookups, [])
                self.assertEqual(store.get_meta("pending_coinbase_order"), old)
            finally:
                store.close()

    def test_pending_order_cannot_be_reconciled_with_different_coinbase_account(self):
        class ScopedRail:
            def __init__(self):
                self.lookups = []
            def order_detail(self, order_id):
                self.lookups.append(order_id)
                return {"order": {"order_id": order_id, "status": "OPEN",
                                  "filled_size": "0"}}

        account_a, account_b = "a" * 64, "b" * 64
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "scoped-pending.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, ScopedRail()
                engine._record_order_intent(
                    product="BTC-USD", side="BUY", client_id="client-a",
                    account_scope=account_a)
                original = store.get_meta("pending_coinbase_order")
                self.assertEqual(json.loads(original)["account_scope"], account_a)
                decision, status = engine._pending_order_decision(scope=account_b)
                self.assertEqual((decision.action, status),
                                 ("HOLD", "ORDER_ACCOUNT_SCOPE_MISMATCH"))
                self.assertEqual(engine.rail.lookups, [])
                self.assertEqual(store.get_meta("pending_coinbase_order"), original)
            finally:
                store.close()

    def test_production_rail_reconciliation_passes_expected_credential_scope(self):
        from src.wallets.advanced_trade import AdvancedTradeSpot

        class Rail(AdvancedTradeSpot):
            def __init__(self):
                self.received_scopes = []

            def account_scope_id(self):
                return "a" * 64

            def order_detail(self, order_id, *, expected_scope=None):
                self.received_scopes.append(expected_scope)
                return {"order": {"order_id": order_id, "status": "OPEN",
                                  "filled_size": "0"}}

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "scope-bound-read.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                store.set_meta("pending_coinbase_order", json.dumps({
                    "order_id": "venue-a", "client_order_id": "client-a",
                    "product": "BTC-USD", "side": "BUY",
                    "account_scope": "a" * 64}))
                decision, status = engine._pending_order_decision(scope="a" * 64)
                self.assertEqual((decision.action, status),
                                 ("HOLD", "ORDER_AWAITING_VERIFICATION"))
                self.assertEqual(rail.received_scopes, ["a" * 64])
                self.assertTrue(store.get_meta("pending_coinbase_order"))
            finally:
                store.close()

    def test_stale_snapshot_scope_blocks_wrong_account_pending_lookup(self):
        class Rail:
            def __init__(self):
                self.scope = "b" * 64
                self.lookups = []

            def account_scope_id(self):
                return self.scope

            def order_detail(self, order_id):
                self.lookups.append(order_id)
                raise AssertionError("Never query the wrong account")

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "stale-snapshot.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                original = json.dumps({
                    "order_id": "venue-a", "client_order_id": "client-a",
                    "product": "BTC-USD", "side": "BUY",
                    "account_scope": "a" * 64})
                store.set_meta("pending_coinbase_order", original)
                # Snapshot was authenticated for A; the rail now uses B.
                decision, status = engine._pending_order_decision(scope="a" * 64)
                self.assertEqual((decision.action, status),
                                 ("HOLD", "ORDER_ACCOUNT_SCOPE_CHANGED"))
                self.assertEqual(rail.lookups, [])
                self.assertEqual(store.get_meta("pending_coinbase_order"), original)
            finally:
                store.close()

    def test_pending_order_switch_during_detail_does_not_clear_receipt(self):
        class Rail:
            def __init__(self):
                self.scope = "a" * 64
                self.lookups = []

            def account_scope_id(self):
                return self.scope

            def order_detail(self, order_id):
                self.lookups.append(order_id)
                self.scope = "b" * 64
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10"}}

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "mid-detail-switch.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                original = json.dumps({
                    "order_id": "venue-a", "client_order_id": "client-a",
                    "product": "BTC-USD", "side": "SELL",
                    "account_scope": "a" * 64})
                store.set_meta("pending_coinbase_order", original)
                decision, status = engine._pending_order_decision(scope="a" * 64)
                self.assertEqual((decision.action, status),
                                 ("HOLD", "ORDER_ACCOUNT_SCOPE_CHANGED"))
                self.assertEqual(rail.lookups, ["venue-a"])
                self.assertEqual(store.get_meta("pending_coinbase_order"), original)
                self.assertIsNone(store.get_meta("last_coinbase_submission_state"))
            finally:
                store.close()

    def test_pending_client_lookup_switch_does_not_replace_receipt(self):
        class Rail:
            def __init__(self):
                self.scope = "a" * 64
                self.details = []

            def account_scope_id(self):
                return self.scope

            def find_order_id_by_client_id(self, **kwargs):
                self.scope = "b" * 64
                return "venue-a"

            def order_detail(self, order_id):
                self.details.append(order_id)
                raise AssertionError("Do not query with switched credentials")

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "mid-finder-switch.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                original = json.dumps({
                    "order_id": None, "client_order_id": "client-a",
                    "product": "BTC-USD", "side": "BUY",
                    "account_scope": "a" * 64})
                store.set_meta("pending_coinbase_order", original)
                decision, status = engine._pending_order_decision(scope="a" * 64)
                self.assertEqual((decision.action, status),
                                 ("HOLD", "ORDER_ACCOUNT_SCOPE_CHANGED"))
                self.assertEqual(rail.details, [])
                self.assertEqual(store.get_meta("pending_coinbase_order"), original)
            finally:
                store.close()

    def test_accepted_unverified_order_keeps_original_account_scope(self):
        class Rail:
            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": "OPEN",
                                  "filled_size": "0"}}
        account_a, account_b = "a" * 64, "b" * 64
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "scoped-accepted.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail()
                engine._record_order_intent(
                    product="BTC-USD", side="SELL", client_id="client-a",
                    account_scope=account_a)
                status, _ = engine._check_or_track_submission(
                    {"success": True, "success_response": {"order_id": "order-a"}},
                    product="BTC-USD", side="SELL", client_id="client-a",
                    account_scope=account_a)
                self.assertEqual(status, "ORDER_ACCEPTED_UNVERIFIED")
                pending = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(pending["account_scope"], account_a)
                self.assertEqual(pending["order_id"], "order-a")
                _, blocked = engine._pending_order_decision(scope=account_b)
                self.assertEqual(blocked, "ORDER_ACCOUNT_SCOPE_MISMATCH")
                self.assertEqual(json.loads(store.get_meta("pending_coinbase_order")),
                                 pending)
            finally:
                store.close()

    def test_ack_detail_switch_keeps_submitting_account_intent(self):
        class Rail:
            def __init__(self):
                self.scope = "a" * 64
                self.details = []
                self.fill_calls = 0

            def account_scope_id(self):
                return self.scope

            def order_detail(self, order_id):
                self.details.append(order_id)
                self.scope = "b" * 64
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10"}}

            def order_fills(self, order_id):
                self.fill_calls += 1
                raise AssertionError("Do not read fills under switched credentials")

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "ack-detail-switch.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                engine._record_order_intent(
                    product="BTC-USD", side="BUY", client_id="client-a",
                    account_scope="a" * 64)
                state, evidence = engine._check_or_track_submission(
                    {"success": True, "success_response": {"order_id": "order-a"}},
                    product="BTC-USD", side="BUY", client_id="client-a",
                    account_scope="a" * 64)
                self.assertEqual(state, "ORDER_ACCEPTED_UNVERIFIED")
                self.assertIn("credentials changed", evidence["reason"])
                self.assertEqual(rail.details, ["order-a"])
                self.assertEqual(rail.fill_calls, 0)
                pending = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(pending["client_order_id"], "client-a")
                self.assertEqual(pending["account_scope"], "a" * 64)
                self.assertEqual(pending["order_id"], "order-a")
                self.assertEqual(store.get_meta("last_coinbase_submission_state"),
                                 "ORDER_ACCEPTED_UNVERIFIED")
            finally:
                store.close()

    def test_ack_fills_switch_cannot_release_pending_receipt(self):
        class Rail:
            def __init__(self):
                self.scope = "a" * 64
                self.fill_calls = 0

            def account_scope_id(self):
                return self.scope

            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10"}}

            def order_fills(self, order_id):
                self.fill_calls += 1
                self.scope = "b" * 64
                return {"fills": []}

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "ack-fills-switch.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                engine._record_order_intent(
                    product="ETH-USD", side="SELL", client_id="client-a",
                    account_scope="a" * 64)
                state, evidence = engine._check_or_track_submission(
                    {"success": True, "success_response": {"order_id": "order-a"}},
                    product="ETH-USD", side="SELL", client_id="client-a",
                    account_scope="a" * 64)
                self.assertEqual(state, "ORDER_ACCEPTED_UNVERIFIED")
                self.assertIn("credentials changed", evidence["reason"])
                self.assertEqual(rail.fill_calls, 1)
                pending = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(pending["client_order_id"], "client-a")
                self.assertEqual(pending["account_scope"], "a" * 64)
                self.assertEqual(pending["order_id"], "order-a")
            finally:
                store.close()

    def test_matching_coinbase_scope_still_verifies_completed_order(self):
        class MatchingRail:
            def __init__(self, scope):
                self.scope = scope
                self.lookups = []

            def account_scope_id(self):
                return self.scope

            def order_detail(self, order_id):
                self.lookups.append(order_id)
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10.0"}}

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "matching-scope.db")
            try:
                account_a = "a" * 64
                rail = MatchingRail(account_a)
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                engine._record_order_intent(
                    product="BTC-USD", side="BUY", client_id="client-a",
                    account_scope=account_a)
                state, receipt = engine._check_or_track_submission(
                    {"success": True, "success_response": {"order_id": "order-a"}},
                    product="BTC-USD", side="BUY", client_id="client-a",
                    account_scope=account_a)
                self.assertEqual(state, "FILLED_VERIFIED")
                self.assertEqual(rail.lookups, ["order-a"])
                self.assertFalse(store.get_meta("pending_coinbase_order"))
            finally:
                store.close()

    def test_switched_credentials_after_ack_preserve_unverified_receipt(self):
        class SwitchingRail:
            def __init__(self):
                self.scope = "a" * 64
                self.lookups = []

            def account_scope_id(self):
                return self.scope

            def order_detail(self, order_id):
                self.lookups.append(order_id)
                raise AssertionError("Do not query prior order with new credentials")

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "post-ack-scope.db")
            try:
                rail = SwitchingRail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                account_a = rail.scope
                engine._record_order_intent(
                    product="BTC-USD", side="BUY", client_id="client-a",
                    account_scope=account_a)
                rail.scope = "b" * 64
                state, receipt = engine._check_or_track_submission(
                    {"success": True, "success_response": {"order_id": "order-a"}},
                    product="BTC-USD", side="BUY", client_id="client-a",
                    account_scope=account_a)
                self.assertEqual(state, "ORDER_ACCEPTED_UNVERIFIED")
                self.assertIn("credentials changed", receipt["reason"])
                self.assertEqual(rail.lookups, [])
                pending = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(pending["account_scope"], account_a)
                self.assertEqual(pending["order_id"], "order-a")
            finally:
                store.close()

    def test_changed_coinbase_credentials_block_both_order_sides(self):
        class SwitchedRail:
            def __init__(self):
                self.orders = []

            def account_scope_id(self):
                return "b" * 64

            def product(self, product_id):
                return {"quote_increment": "0.01", "quote_min_size": "1",
                        "base_increment": "0.00001", "base_min_size": "0.001"}

            def preview_market_buy(self, product_id, amount):
                return {"preview_id": "offline-buy"}

            def preview_market_sell(self, product_id, amount):
                return {"preview_id": "offline-sell"}

            def market_buy(self, *args, **kwargs):
                self.orders.append(("BUY", args, kwargs))
                raise AssertionError("Wrong-account BUY must never be submitted")

            def market_sell(self, *args, **kwargs):
                self.orders.append(("SELL", args, kwargs))
                raise AssertionError("Wrong-account SELL must never be submitted")

            def order_detail(self, order_id):
                raise AssertionError("No order should have been submitted")

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "switched-credentials.db")
            try:
                rail = SwitchedRail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                original_scope = "a" * 64
                with self.assertRaisesRegex(RuntimeError, "credentials changed"):
                    engine._execute_buy(
                        Decision("BUY", "BTC-USD", 1.0, "offline test"),
                        {"cash_by_quote": {"USD": 25},
                         "authenticated_scope": original_scope})
                with self.assertRaisesRegex(RuntimeError, "credentials changed"):
                    engine._execute_sell(
                        Decision("SELL", "BTC-USD", 1.0, "offline test"),
                        {"positions": {"BTC-USD": {"available_qty": 0.5}},
                         "authenticated_scope": original_scope})
                self.assertEqual(rail.orders, [])
                self.assertFalse(store.get_meta("pending_coinbase_order"))
            finally:
                store.close()

    def test_network_timeout_preserves_order_intent_without_resubmission(self):
        class AmbiguousRail:
            def product(self, product_id):
                return {"quote_increment":"0.01","quote_min_size":"1"}
            def preview_market_buy(self, product_id, qty):
                return {"preview_id":"local-test"}
            def market_buy(self, product_id, qty, client_order_id):
                raise ConnectionError("No response; Coinbase outcome unknown")
            def order_detail(self, order_id):
                raise AssertionError("No order ID was received")
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td)/"ambiguous.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = AmbiguousRail()
                with self.assertRaises(ConnectionError):
                    engine._execute_buy(
                        Decision("BUY", "ALEO-USD", 1.0, "test"),
                        {"cash_by_quote":{"USD":25},
                         "authenticated_scope":"a"*64})
                pending=store.get_meta("pending_coinbase_order")
                self.assertTrue(pending)
                self.assertIn("client_order_id",pending)
                self.assertEqual(json.loads(pending)["account_scope"], "a"*64)
                decision,status=engine._pending_order_decision(scope="a"*64)
                self.assertEqual(status,"ORDER_AWAITING_VERIFICATION")
                self.assertEqual(decision.action,"HOLD")
            finally:
                store.close()

    def test_failed_authenticated_order_is_not_called_filled(self):
        detail={"order":{"order_id":"123","status":"CANCELLED",
                         "filled_size":"0"}}
        self.assertEqual(verified_order(detail,"123")["state"],"VENUE_NOT_FILLED")

    def test_cancelled_partial_fill_must_not_be_misreported_as_zero_fill(self):
        detail={"order":{"order_id":"123","status":"CANCELLED",
                         "filled_size":"0.003"}}
        self.assertEqual(
            verified_order(detail,"123")["state"],
            "PARTIAL_FILL_REQUIRES_RECONCILIATION")



    def test_malformed_or_absent_filled_value_cannot_verify_order(self):
        for bad in (None, "NaN", "Infinity", "-1", "0", "invalid"):
            with self.subTest(filled_value=bad):
                order = {"order_id": "cb-123", "status": "FILLED",
                         "filled_size": "0.125"}
                if bad is not None:
                    order["filled_value"] = bad
                receipt = verified_order({"order": order}, "cb-123")
                self.assertEqual(receipt["state"], "FILLED_EVIDENCE_INCOMPLETE")

    def test_filled_order_missing_identity_cannot_verify(self):
        order = {"status": "FILLED", "filled_size": "0.125",
                 "filled_value": "12.50"}
        self.assertEqual(verified_order({"order": order}, "cb-123")["state"],
                         "UNVERIFIED")

    def test_malformed_filled_value_keeps_original_client_order_id(self):
        class BadFillRail:
            def order_detail(self, order_id):
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.5", "filled_value": "NaN"}}
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "bad-fill.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, BadFillRail()
                intent = ('{"order_id":"cb-123","client_order_id":"client-123",'
                          '"product":"BTC-USD","side":"BUY"}')
                store.set_meta("pending_coinbase_order", intent)
                decision, status = engine._pending_order_decision()
                self.assertEqual(status, "ORDER_AWAITING_VERIFICATION")
                self.assertEqual(decision.action, "HOLD")
                self.assertIn("client-123", store.get_meta("pending_coinbase_order"))
            finally:
                store.close()


    def test_preclient_scope_switch_clears_unsent_buy_and_sell(self):
        from unittest.mock import patch
        from src.wallets.advanced_trade import (
            AdvancedTradeSpot, AdvancedTradeVault, CoinbasePreSubmissionScopeMismatch)

        class Vault(AdvancedTradeVault):
            def __init__(self):
                self.key = "a"
            def _credentials(self):
                return ("key-" + self.key, "secret-" + self.key)

        class Rail(AdvancedTradeSpot):
            def product(self, product_id):
                return {"quote_increment": "0.01", "quote_min_size": "1",
                        "base_increment": "0.0001", "base_min_size": "0.0001"}
            def preview_market_buy(self, product_id, quote_size):
                return {}
            def preview_market_sell(self, product_id, base_size):
                return {}

        for side in ("BUY", "SELL"):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as td:
                store = StateStore(Path(td) / "unsent.db")
                try:
                    vault = Vault()
                    rail = Rail(vault)
                    engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                    engine.store, engine.rail = store, rail
                    scope = rail.account_scope_id()
                    original_check = engine._require_snapshot_order_scope
                    def switch_after_check(snapshot):
                        original_check(snapshot)
                        vault.key = "b"
                    engine._require_snapshot_order_scope = switch_after_check
                    snapshot = {"authenticated_scope": scope,
                                "cash_by_quote": {"USD": 100},
                                "positions": {"BTC-USD": {"available_qty": "0.1"}}}
                    with patch("src.wallets.advanced_trade.RESTClient") as client:
                        with self.assertRaises(CoinbasePreSubmissionScopeMismatch):
                            getattr(engine, "_execute_" + side.lower())(
                                Decision(side, "BTC-USD", 1.0, "offline"), snapshot)
                        client.assert_not_called()
                    self.assertEqual(store.get_meta("pending_coinbase_order"), "")
                    self.assertEqual(store.get_meta("last_coinbase_submission_state"),
                                     "NOT_SUBMITTED_SCOPE_CHANGED")
                finally:
                    store.close()

    def test_network_timeout_preserves_pending_client_id(self):
        from src.wallets.advanced_trade import AdvancedTradeSpot
        class Rail(AdvancedTradeSpot):
            def __init__(self):
                self.scope = "a" * 64
            def account_scope_id(self):
                return self.scope
            def product(self, product_id):
                return {"quote_increment": "0.01", "quote_min_size": "1"}
            def preview_market_buy(self, product_id, quote_size):
                return {}
            def market_buy(self, product_id, quote_size, client_order_id,
                           *, expected_scope=None):
                self.sent_client_id = client_order_id
                raise TimeoutError("Unknown venue outcome")
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "ambiguous.db")
            try:
                rail = Rail()
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, rail
                with self.assertRaises(TimeoutError):
                    engine._execute_buy(
                        Decision("BUY", "BTC-USD", 1.0, "offline"),
                        {"authenticated_scope": "a" * 64,
                         "cash_by_quote": {"USD": 100}})
                receipt = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(receipt["client_order_id"], rail.sent_client_id)
            finally:
                store.close()


    def test_delayed_reconciliation_cannot_clear_newer_order_receipt(self):
        # Another desktop connection may finish A, then claim B while the
        # original order-detail HTTP call is still outstanding.
        class Rail:
            def __init__(self, store):
                self.store = store
            def order_detail(self, order_id):
                self.store.set_meta("pending_coinbase_order", json.dumps({
                    "order_id": None, "client_order_id": "client-b",
                    "product": "ETH-USD", "side": "BUY"}))
                return {"order": {"order_id": order_id, "status": "FILLED",
                                  "filled_size": "0.1", "filled_value": "10"}}
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "delayed-reconcile.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail(store)
                store.set_meta("pending_coinbase_order", json.dumps({
                    "order_id": "order-a", "client_order_id": "client-a",
                    "product": "BTC-USD", "side": "BUY"}))
                decision, status = engine._pending_order_decision()
                self.assertEqual((decision.action, status),
                                 ("HOLD", "ORDER_JOURNAL_CHANGED"))
                receipt = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(receipt["client_order_id"], "client-b")
                self.assertIsNone(store.get_meta("last_coinbase_submission_state"))
            finally:
                store.close()

    def test_delayed_ack_cannot_overwrite_newer_order_receipt(self):
        class Rail:
            def __init__(self, store):
                self.store = store
            def order_detail(self, order_id):
                self.store.set_meta("pending_coinbase_order", json.dumps({
                    "order_id": None, "client_order_id": "client-b",
                    "product": "ETH-USD", "side": "SELL"}))
                return {"order": {"order_id": order_id, "status": "OPEN",
                                  "filled_size": "0"}}
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "delayed-ack.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail(store)
                engine._record_order_intent(product="BTC-USD", side="BUY",
                                            client_id="client-a")
                status, evidence = engine._check_or_track_submission(
                    {"success": True, "success_response": {"order_id": "order-a"}},
                    product="BTC-USD", side="BUY", client_id="client-a")
                self.assertEqual(status, "ORDER_ACCEPTED_UNVERIFIED")
                self.assertIn("journal changed", evidence["reason"])
                receipt = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(receipt["client_order_id"], "client-b")
            finally:
                store.close()

    def test_explicit_rejection_cannot_clear_another_clients_receipt(self):
        from src.wallets.advanced_trade import CoinbaseExplicitRejection
        class Rail:
            def __init__(self, store):
                self.store = store
            def order_detail(self, order_id):
                raise AssertionError("No detail lookup after explicit rejection")
            def product(self, product_id):
                return {"quote_increment": "0.01", "quote_min_size": "1"}
            def preview_market_buy(self, product_id, amount):
                return {"preview_id": "offline"}
            def market_buy(self, product_id, amount, client_order_id):
                self.store.set_meta("pending_coinbase_order", json.dumps({
                    "order_id": None, "client_order_id": "client-b",
                    "product": "ETH-USD", "side": "SELL"}))
                raise CoinbaseExplicitRejection("Rejected before acceptance")
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "delayed-reject.db")
            try:
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store, engine.rail = store, Rail(store)
                with self.assertRaises(CoinbaseExplicitRejection):
                    engine._execute_buy(
                        Decision("BUY", "BTC-USD", 1.0, "offline"),
                        {"cash_by_quote": {"USD": 25}})
                receipt = json.loads(store.get_meta("pending_coinbase_order"))
                self.assertEqual(receipt["client_order_id"], "client-b")
            finally:
                store.close()

if __name__=="__main__":
    unittest.main()
