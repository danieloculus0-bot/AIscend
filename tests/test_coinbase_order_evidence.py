import json
import tempfile
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
                decision,status=engine._pending_order_decision()
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


if __name__=="__main__":
    unittest.main()
