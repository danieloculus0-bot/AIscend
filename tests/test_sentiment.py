import unittest

from src.research import MarketResearch, ResearchDecider
from src.sentiment import HumanSignalResearch


class SentimentTests(unittest.TestCase):
    def test_fomo_is_continuation_fuel_when_price_confirms(self):
        edge = MarketResearch._crowd_edge(
            fomo=82.0,
            momentum_5m=0.8,
            momentum_1h=0.9,
            volume_heat=0.7,
        )
        self.assertGreater(edge, 0)

    def test_fomo_turns_negative_on_hot_crowd_reversal(self):
        edge = MarketResearch._crowd_edge(
            fomo=92.0,
            momentum_5m=-0.8,
            momentum_1h=0.3,
            volume_heat=0.9,
        )
        self.assertLess(edge, 0)

    def test_extreme_fear_reversal_can_be_positive_edge(self):
        edge = MarketResearch._crowd_edge(
            fomo=14.0,
            momentum_5m=0.7,
            momentum_1h=0.2,
            volume_heat=0.1,
        )
        self.assertGreater(edge, 0)

    def test_sentiment_lexicon_direction(self):
        positive = HumanSignalResearch._sentiment(
            ["Ethereum rally and adoption surge toward record high"]
        )
        negative = HumanSignalResearch._sentiment(
            ["Crypto crash after hack, liquidation and market panic"]
        )
        self.assertGreater(positive, 0)
        self.assertLess(negative, 0)

    def test_decider_can_ride_confirmed_fomo(self):
        decision = ResearchDecider().decide(
            {
                "cash": 25.0,
                "positions": {},
                "research": {
                    "available": True,
                    "composite_score": 0.13,
                    "confidence": 0.45,
                    "thesis": "confirmed momentum",
                    "human": {"fomo_index": 76.0, "crowd_regime": "FOMO"},
                    "eth": {
                        "return_5m": 0.01,
                        "return_1h": 0.02,
                        "volume_ratio": 1.4,
                    },
                },
            }
        )
        self.assertEqual(decision.action, "BUY")

    def test_decider_does_not_blindly_chase_euphoria(self):
        decision = ResearchDecider().decide(
            {
                "cash": 25.0,
                "positions": {},
                "research": {
                    "available": True,
                    "composite_score": 0.18,
                    "confidence": 0.55,
                    "thesis": "crowded tape rolling over",
                    "human": {"fomo_index": 94.0, "crowd_regime": "EUPHORIA"},
                    "eth": {
                        "return_5m": -0.006,
                        "return_1h": 0.01,
                        "volume_ratio": 1.1,
                    },
                },
            }
        )
        self.assertEqual(decision.action, "HOLD")


if __name__ == "__main__":
    unittest.main()
