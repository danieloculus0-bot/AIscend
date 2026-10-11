import unittest
from scripts.bean_historical_replay import run, assess, START


def fixture():
    bars = []
    for t in range(180):
        prices = {}
        for i, p in enumerate(("BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD")):
            value = 100 * (1 + 0.0005 * (i + 1)) ** t
            prices[p] = {"open": value, "close": value * 1.0001}
        bars.append({"ts": t * 3600, "prices": prices})
    return {"source": "test-fixture-not-real", "pairs": list(bars[0]["prices"]), "bars": bars}


class HistoricalReplayTests(unittest.TestCase):
    def test_replay_deterministic(self):
        d = fixture()
        first, second = assess(d), assess(d)
        first.pop("as_of")
        second.pop("as_of")
        self.assertEqual(first, second)

    def test_no_forced_level_one(self):
        result = assess(fixture())
        self.assertFalse(result["level_one_achieved"])
        self.assertLess(result["holdout"]["final"], 100000)

    def test_transaction_costs_are_applied(self):
        bars = fixture()["bars"]
        hold = run(bars, "buy_hold", 3, 0)
        cash = run(bars, "cash", 3, 0)
        self.assertEqual(cash["final"], START)
        self.assertGreater(hold["trades"], 0)

    def test_missing_data_rejected(self):
        with self.assertRaises(ValueError):
            assess({"bars": [], "source": "missing"})


if __name__ == "__main__":
    unittest.main()
