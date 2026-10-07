import tempfile
import unittest
from pathlib import Path

from src.core import StateStore
from src.listing_sentinel import CoinbaseListingSentinel


class ListingSentinelTests(unittest.TestCase):
    def test_detects_new_product_and_trading_enablement(self):
        payloads = [
            [
                {
                    "id": "AAA-USD",
                    "base_currency": "AAA",
                    "quote_currency": "USD",
                    "status": "online",
                    "trading_disabled": False,
                }
            ],
            [
                {
                    "id": "AAA-USD",
                    "base_currency": "AAA",
                    "quote_currency": "USD",
                    "status": "online",
                    "trading_disabled": False,
                },
                {
                    "id": "SHIT-EUR",
                    "base_currency": "SHIT",
                    "quote_currency": "EUR",
                    "status": "online",
                    "trading_disabled": True,
                },
            ],
            [
                {
                    "id": "AAA-USD",
                    "base_currency": "AAA",
                    "quote_currency": "USD",
                    "status": "online",
                    "trading_disabled": False,
                },
                {
                    "id": "SHIT-EUR",
                    "base_currency": "SHIT",
                    "quote_currency": "EUR",
                    "status": "online",
                    "trading_disabled": False,
                },
            ],
        ]
        index = {"value": 0}

        def fetch(path):
            self.assertEqual(path, "/products")
            value = payloads[index["value"]]
            index["value"] += 1
            return value

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "sentinel.db")
            sentinel = CoinbaseListingSentinel(
                store,
                fetch_json=fetch,
                poll_seconds=10,
            )

            first = sentinel.poll(force=True)
            self.assertTrue(first["baseline_created"])
            self.assertEqual(first["new_products"], [])

            second = sentinel.poll(force=True)
            self.assertEqual(second["new_products"][0]["product"], "SHIT-EUR")
            self.assertEqual(second["new_products"][0]["event"], "NEW_PRODUCT")
            self.assertEqual(second["new_products"][0]["stage"], "DISCOVERED")

            third = sentinel.poll(force=True)
            self.assertEqual(third["changes"][0]["event"], "TRADING_ENABLED")
            self.assertEqual(third["changes"][0]["stage"], "FULL_TRADING")
            self.assertTrue(any(e["product"] == "SHIT-EUR" for e in third["hot"]))
            store.close()


if __name__ == "__main__":
    unittest.main()
