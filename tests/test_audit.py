import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.audit import AuditTrail


class AuditTrailTests(unittest.TestCase):
    def test_trade_writes_jsonl_and_csv_with_precision_and_redaction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audit = AuditTrail(root)
            event = audit.record_trade(
                venue="coinbase",
                rail="coinbase-advanced",
                network="coinbase",
                side="BUY",
                product_id="BTC-USDC",
                base_asset="BTC",
                quote_asset="USDC",
                base_quantity=Decimal("0.0001234500"),
                quote_quantity=Decimal("12.3400"),
                unit_price_quote=Decimal("99959.497772")
                ,
                order_id="order-1",
                client_order_id="client-1",
                status="complete",
                rationale="test buy",
                source="unit_test",
                raw_provider_response={
                    "order_id": "order-1",
                    "api_key": "do-not-store",
                    "nested": {"private_key": "also-do-not-store"},
                },
            )

            jsonl = next(root.glob("aiscend-audit-*.jsonl"))
            csv_path = next(root.glob("aiscend-audit-*.csv"))

            rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_id"], event["event_id"])
            self.assertEqual(rows[0]["base_quantity"], "0.0001234500")
            self.assertEqual(rows[0]["quote_quantity"], "12.3400")
            self.assertEqual(rows[0]["raw_provider_response"]["api_key"], "[REDACTED]")
            self.assertEqual(
                rows[0]["raw_provider_response"]["nested"]["private_key"],
                "[REDACTED]",
            )

            with csv_path.open(newline="", encoding="utf-8") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(len(csv_rows), 1)
            self.assertEqual(csv_rows[0]["order_id"], "order-1")
            self.assertEqual(csv_rows[0]["event_type"], "trade")

    def test_transfer_is_distinct_from_trade(self):
        with tempfile.TemporaryDirectory() as td:
            audit = AuditTrail(Path(td))
            event = audit.record_transfer(
                venue="coinbase",
                rail="wallet-bridge",
                network="base",
                asset="USDC",
                quantity=Decimal("25.00"),
                direction="BASE_TO_ADVANCED",
                from_location="base-smart-wallet",
                to_location="coinbase-advanced",
                transaction_id="0xabc",
                status="complete",
                source="unit_test",
            )
            self.assertEqual(event["event_type"], "transfer")
            self.assertEqual(event["asset"], "USDC")
            self.assertEqual(event["base_quantity"], "25.00")
            self.assertEqual(event["side"], "BASE_TO_ADVANCED")


if __name__ == "__main__":
    unittest.main()
