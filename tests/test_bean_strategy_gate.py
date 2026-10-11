import unittest
from scripts.bean_strategy_gate import judge


def report(strategy=None, win=True, drawdown=0.12):
    return {"historical_source": "Coinbase public candles",
            "strategy": strategy or {"rule":"momentum","lookback":6,"threshold":0.01},
            "beats_btc_holdout": win, "beats_cash_holdout": win,
            "holdout": {"max_drawdown": drawdown, "trades": 8}}


class StrategyGateTests(unittest.TestCase):
    def test_no_one_window_winner(self):
        self.assertFalse(judge([report()])["accepted"])

    def test_two_independent_wins(self):
        self.assertTrue(judge([report(), report()])["accepted"])

    def test_benchmark_failure(self):
        self.assertFalse(judge([report(), report(win=False)])["accepted"])

    def test_disagreement(self):
        self.assertFalse(judge([report(), report({"rule":"reversal","lookback":3,"threshold":0})])["accepted"])

    def test_excess_drawdown(self):
        self.assertFalse(judge([report(), report(drawdown=0.5)])["accepted"])

    def test_synthetic_rejected(self):
        bad = report()
        bad["historical_source"] = "synthetic"
        self.assertFalse(judge([report(), bad])["accepted"])


if __name__ == "__main__":
    unittest.main()
