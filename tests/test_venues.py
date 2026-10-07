import unittest

from src.opportunities import OpportunityUniverse
from src.venues import capability_snapshot


class FakeUniverse(OpportunityUniverse):
    def __init__(self):
        pass

    def _get_json(self, path):
        return {
            "markets": [
                {
                    "ticker": "TEST-1",
                    "title": "Will the thing happen?",
                    "yes_bid_dollars": "0.40",
                    "yes_ask_dollars": "0.45",
                    "volume_24h_fp": "10000",
                    "volume_fp": "12000",
                    "open_interest_fp": "5000",
                    "close_time": "2026-12-31T00:00:00Z",
                }
            ]
        }


class VenueTests(unittest.TestCase):
    def test_first_launch_exposes_all_market_lanes(self):
        data = capability_snapshot()
        keys = {v["key"] for v in data["venues"]}
        self.assertEqual(keys, {"crypto", "predictions", "stocks_etfs", "options"})
        self.assertEqual(data["execution_ready"], 1)

    def test_prediction_market_scan_normalizes_public_market(self):
        rows = FakeUniverse()._prediction_markets()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.venue, "predictions")
        self.assertEqual(row.instrument, "TEST-1")
        self.assertFalse(row.execution_ready)
        self.assertAlmostEqual(row.details["market_probability_mid"], 0.425)


if __name__ == "__main__":
    unittest.main()
