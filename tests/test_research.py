import unittest

from src.research import MarketResearch, ResearchDecider


class FakeResearch(MarketResearch):
    def __init__(self):
        pass

    def _get_json(self, path):
        product = "BTC" if "BTC-USD" in path else "ETH"
        if "candles" in path:
            base = 100.0 if product == "ETH" else 1000.0
            rows = []
            for i in range(100):
                price = base * (1.0 + i * 0.001)
                rows.append([i, price * 0.99, price * 1.01, price, price, 10 + i])
            return list(reversed(rows))
        if "ticker" in path:
            return {"price": "110.0" if product == "ETH" else "1100.0"}
        if "book" in path:
            px = 110.0 if product == "ETH" else 1100.0
            return {"bids": [[str(px - 0.01), "1", 1]], "asks": [[str(px + 0.01), "1", 1]]}
        raise AssertionError(path)


class ResearchTests(unittest.TestCase):
    def test_collect_produces_prediction(self):
        pack = FakeResearch().collect()
        self.assertTrue(pack.available)
        self.assertEqual(pack.prediction, "BULLISH")
        self.assertGreater(pack.composite_score, 0)

    def test_decider_uses_research(self):
        decision = ResearchDecider().decide(
            {
                "cash": 25.0,
                "positions": {},
                "research": {
                    "available": True,
                    "composite_score": 0.7,
                    "confidence": 0.7,
                    "thesis": "strong test regime",
                },
            }
        )
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.symbol, "WETH")
        self.assertGreater(decision.fraction, 0)


if __name__ == "__main__":
    unittest.main()
