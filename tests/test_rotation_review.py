import unittest
from src.rotation_review import review_rotation


class RotationReviewTests(unittest.TestCase):
    def test_weak_holding_with_stronger_candidate_gets_review_not_trade(self):
        result = review_rotation("WEAK-USDC", {"decision_score": -0.03},
            {"product": "BETTER-USDC", "decision_score": 0.35,
             "spread_bps": 8, "volume_ratio": 1.5})
        self.assertEqual(result.action, "REVIEW")

    def test_zero_score_preserved(self):
        result = review_rotation("A-USDC", {"score": -0.7, "decision_score": 0},
            {"product": "B-USDC", "score": 0.02, "spread_bps": 5, "volume_ratio": 1})
        self.assertEqual(result.held_score, 0)
        self.assertEqual(result.action, "HOLD")

    def test_missing_held_signal_is_unknown_not_sell(self):
        self.assertEqual(review_rotation("A-USDC", None,
            {"product": "B-USDC", "score": 0.9, "spread_bps": 5,
             "volume_ratio": 2}).action, "RESEARCH")

    def test_missing_liquidity_cannot_claim_actionable_rotation(self):
        self.assertEqual(review_rotation("A-USDC", {"score": 0},
            {"product": "B-USDC", "score": 0.8}).action, "RESEARCH")

    def test_bad_spread_blocks_review(self):
        self.assertEqual(review_rotation("A-USDC", {"score": 0},
            {"product": "B-USDC", "score": 0.8,
             "spread_bps": 100, "volume_ratio": 2}).action, "HOLD")


if __name__ == "__main__":
    unittest.main()
