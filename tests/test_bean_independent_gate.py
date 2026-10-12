import unittest
from scripts.bean_strategy_gate import judge

class IndependentWindows(unittest.TestCase):
    def test_duplicate_or_unproven_reports_never_pass(self):
        sample = {"historical_source": "Coinbase public candles",
                  "strategy": {"rule": "momentum"},
                  "beats_btc_holdout": True, "beats_cash_holdout": True,
                  "holdout": {"trades": 8, "max_drawdown": 0.01}}
        self.assertFalse(judge([sample, sample])["accepted"])

    def test_missing_fee_and_window_receipts_fail_closed(self):
        self.assertFalse(judge([{}, {}])["accepted"])

if __name__ == "__main__":
    unittest.main()
