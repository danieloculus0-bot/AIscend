import sqlite3
import unittest

from src.bean import BeanClaim, BeanMemory


class BeanTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.bean = BeanMemory(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_prediction_resolution_updates_source_trust(self):
        self.bean.add_prediction(
            subject="ETH",
            horizon_seconds=0,
            start_price=100.0,
            predicted_direction=1,
            predicted_strength=0.7,
            confidence=0.8,
            source_mix={"technical": 0.6, "human": 0.3},
        )
        resolved = self.bean.resolve_due({"ETH": 103.0})
        self.assertEqual(resolved, 1)
        snap = self.bean.snapshot()
        self.assertEqual(snap["resolved_predictions"], 1)
        self.assertGreater(self.bean.trust("technical", 0), 0.5)

    def test_repeated_claims_are_compressed(self):
        claim = BeanClaim(
            "OBSERVATION",
            "ETH",
            "ETH momentum remains negative.",
            0.8,
            "coinbase_market",
            ("price",),
        )
        first = self.bean.add_claim(claim, dedupe_seconds=600)
        second = self.bean.add_claim(
            BeanClaim(
                "OBSERVATION",
                "ETH",
                "ETH momentum remains negative with updated price.",
                0.9,
                "coinbase_market",
                ("price",),
            ),
            dedupe_seconds=600,
        )
        self.assertEqual(first, second)
        snap = self.bean.snapshot()
        self.assertEqual(snap["claims"], 1)
        self.assertEqual(snap["compressed_repeats"], 1)

    def test_individual_signal_family_can_earn_trust(self):
        self.bean.add_prediction(
            subject="ETH",
            horizon_seconds=0,
            start_price=100.0,
            predicted_direction=-1,
            predicted_strength=0.8,
            confidence=0.7,
            source_mix={"social": -1.0, "news": -0.4, "technical": -0.5},
        )
        self.bean.resolve_due({"ETH": 97.0})
        self.assertGreater(self.bean.trust("social", 0), 0.5)
        self.assertGreater(self.bean.trust("news", 0), 0.5)

    def test_contradiction_is_retained(self):
        self.bean.record_contradiction(
            "ETH", "technical", 0.4, "human", -0.5, 0.45, "disagreement"
        )
        self.assertEqual(self.bean.snapshot()["contradictions"], 1)


if __name__ == "__main__":
    unittest.main()
