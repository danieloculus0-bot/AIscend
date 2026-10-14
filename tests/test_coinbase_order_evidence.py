import tempfile
import unittest
from pathlib import Path

from src.coinbase_order_evidence import accepted_order_id, verified_order
from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore


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

    def test_pending_coinbase_order_blocks_new_orders_until_fill_verified(self):
        class Rail:
            def __init__(self):
                self.state="OPEN"
            def order_detail(self,order_id):
                return {"order":{"order_id":order_id,"status":self.state,
                                 "filled_size":"0.05" if self.state=="FILLED" else "0"}}
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



if __name__=="__main__":
    unittest.main()
