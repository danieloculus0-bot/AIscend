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
                    "event_ticker": "KXNFL-TEST",
                    "series_ticker": "KXNFL",
                    "category": "Sports",
                }
            ]
        }


class VenueTests(unittest.TestCase):
    def test_first_launch_exposes_all_market_lanes(self):
        data = capability_snapshot()
        keys = {v["key"] for v in data["venues"]}
        self.assertEqual(
            keys,
            {"crypto", "predictions", "stocks_etfs", "futures", "perpetuals", "options"},
        )
        self.assertEqual(data["enabled"], 6)
        self.assertTrue(all(v["enabled"] for v in data["venues"]))
        self.assertEqual(data["execution_ready"], 1)

    def test_prediction_market_scan_paginates_open_universe(self):
        class PagedUniverse(OpportunityUniverse):
            def __init__(self):
                super().__init__(max_prediction_pages=3)
                self.calls = []

            def _get_json(self, path):
                self.calls.append(path)
                if "cursor=next" in path:
                    return {
                        "markets": [
                            {
                                "ticker": "NBA-2",
                                "title": "NBA second market",
                                "yes_bid_dollars": "0.48",
                                "yes_ask_dollars": "0.50",
                                "volume_24h_fp": "20000",
                                "open_interest_fp": "8000",
                                "series_ticker": "KXNBA",
                            }
                        ],
                        "cursor": "",
                    }
                return {
                    "markets": [
                        {
                            "ticker": "NFL-1",
                            "title": "NFL first market",
                            "yes_bid_dollars": "0.49",
                            "yes_ask_dollars": "0.51",
                            "volume_24h_fp": "30000",
                            "open_interest_fp": "12000",
                            "series_ticker": "KXNFL",
                        }
                    ],
                    "cursor": "next",
                }

        universe = PagedUniverse()
        rows = universe._prediction_markets()
        self.assertEqual(len(rows), 2)
        self.assertEqual(universe._prediction_scan_meta["open_markets_seen"], 2)
        self.assertEqual(universe._prediction_scan_meta["pages_scanned"], 2)
        self.assertEqual(universe._prediction_scan_meta["sports_counts"]["NFL"], 1)
        self.assertEqual(universe._prediction_scan_meta["sports_counts"]["NBA"], 1)
        self.assertEqual(len(universe.calls), 2)

    def test_specific_sports_leagues_beat_generic_keywords(self):
        self.assertEqual(
            OpportunityUniverse._prediction_category(
                {"title": "WNBA basketball championship", "series_ticker": "KXWNBA"}
            ),
            ("sports", "WNBA"),
        )
        self.assertEqual(
            OpportunityUniverse._prediction_category(
                {"title": "College football playoff", "series_ticker": "KXNCAAF"}
            ),
            ("sports", "NCAAF"),
        )

    def test_prediction_market_scan_normalizes_public_market(self):
        rows = FakeUniverse()._prediction_markets()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.venue, "predictions")
        self.assertEqual(row.instrument, "TEST-1")
        self.assertFalse(row.execution_ready)
        self.assertAlmostEqual(row.details["market_probability_mid"], 0.425)
        self.assertEqual(row.score_kind, "market_quality")
        self.assertEqual(row.details["category"], "sports")
        self.assertEqual(row.details["sport"], "NFL")
        self.assertGreater(row.priority, 0.0)
        self.assertIsNone(row.details["directional_edge"])


if __name__ == "__main__":
    unittest.main()
