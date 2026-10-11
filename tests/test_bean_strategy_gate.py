import unittest
from scripts.bean_strategy_gate import judge


def report(strategy=None, win=True, drawdown=0.12, *, start=1000, end=2000,
           fee=0.006, final=28.0, btc=26.0, cash=25.0):
    return {"historical_source": "Coinbase public candles",
            "strategy": strategy or {"rule": "momentum", "lookback": 6, "threshold": 0.01},
            "training_end_ts": start - 3600,
            "holdout_window": {"start_ts": start, "end_ts": end},
            "cost_per_side": fee, "starting_bankroll": 25.0,
            "beats_btc_holdout": win, "beats_cash_holdout": win,
            "buy_hold_btc": {"final": btc}, "cash": {"final": cash},
            "holdout": {"final": final, "max_drawdown": drawdown, "trades": 8}}


class StrategyGateTests(unittest.TestCase):
    def test_no_one_window_winner(self):
        self.assertFalse(judge([report()])["accepted"])

    def test_two_independent_wins(self):
        self.assertTrue(judge([report(), report(start=3000, end=4000)])["accepted"])

    def test_overlap_rejected_even_when_flags_claim_success(self):
        self.assertIn("overlap", judge([report(), report(start=1500, end=3000)])["reason"].lower())

    def test_duplicate_holdout_rejected(self):
        self.assertFalse(judge([report(), report()])["accepted"])

    def test_missing_provenance_rejected(self):
        bad = report(start=3000, end=4000)
        del bad["holdout_window"]
        self.assertFalse(judge([report(), bad])["accepted"])

    def test_invalid_chronology_rejected(self):
        bad = report(start=3000, end=4000)
        bad["training_end_ts"] = 3000
        self.assertFalse(judge([report(), bad])["accepted"])

    def test_benchmark_failure(self):
        self.assertFalse(judge([report(), report(win=False, start=3000, end=4000)])["accepted"])

    def test_benchmark_flags_cannot_override_numerical_loss(self):
        self.assertFalse(judge([report(), report(start=3000, end=4000, final=24)])["accepted"])

    def test_fees_must_be_positive_and_consistent(self):
        self.assertFalse(judge([report(), report(start=3000, end=4000, fee=0)])["accepted"])
        self.assertFalse(judge([report(), report(start=3000, end=4000, fee=0.001)])["accepted"])

    def test_nonfinite_numbers_rejected(self):
        self.assertFalse(judge([report(), report(start=3000, end=4000, final=float("nan"))])["accepted"])

    def test_disagreement(self):
        self.assertFalse(judge([report(), report({"rule": "reversal", "lookback": 3, "threshold": 0}, start=3000, end=4000)])["accepted"])

    def test_excess_drawdown(self):
        self.assertFalse(judge([report(), report(start=3000, end=4000, drawdown=0.5)])["accepted"])

    def test_synthetic_rejected(self):
        bad = report(start=3000, end=4000)
        bad["historical_source"] = "synthetic"
        self.assertFalse(judge([report(), bad])["accepted"])


if __name__ == "__main__":
    unittest.main()
