"""Causal, cost-aware independent BEAN research candidate tests. Synthetic data only."""
import unittest
from src import bean_edge_lab as lab


def candles(n=340, per_bar=0.007):
    result = {}
    price = 100.
    for j in range(n):
        opening = price
        price *= 1+per_bar
        result[j*lab.BAR] = {"ts": j*lab.BAR, "open": opening,
                             "close": price, "volume": 100}
    return result


class OpportunityTests(unittest.TestCase):
    def test_strong_trend_gives_evidence_based_paper_entries(self):
        report = lab.experiment(candles())
        self.assertGreater(report["completed_roundtrips"], 0)
        self.assertGreater(report["fee_net_return"], 0)
        self.assertFalse(report["promotion_authorized"])
        self.assertTrue(all(t["exit_ts"] > t["entry_ts"] > t["decision_ts"]
                            for t in report["hypothetical_trades"]))

    def test_flat_market_abstains_under_real_fees(self):
        report = lab.experiment(candles(per_bar=0))
        self.assertEqual(report["completed_roundtrips"], 0)
        self.assertEqual(report["fee_net_return"], 0)
        self.assertGreater(report["abstentions"], 0)

    def test_fee_stress_overrides_bullish_regime(self):
        report = lab.experiment(candles(), fee_bps=1000)
        self.assertEqual(report["completed_roundtrips"], 0)

    def test_cannot_learn_from_a_future_open(self):
        original = lab.normalize(candles())
        at = 250
        first = lab.forecast(original, at)
        altered = lab.normalize(candles())
        altered[at+1]["open"] *= 100
        altered[at+9]["open"] *= 100
        self.assertEqual(first, lab.forecast(altered, at))
        samples = lab.matured_samples(original, at)
        self.assertLess(sum(map(len, samples.values())), 40)
        self.assertGreater(sum(map(len, samples.values())), 0)

    def test_no_trade_without_matured_comparable_samples(self):
        rows = lab.normalize(candles(n=60))
        result = lab.forecast(rows, 25)
        self.assertEqual(result["decision"], "CASH")
        self.assertEqual(result["reason"], "INSUFFICIENT_MATURED_EXAMPLES")

    def test_gap_is_not_stitched(self):
        raw = candles()
        del raw[100*lab.BAR]
        with self.assertRaisesRegex(ValueError, "INVALID_OR_MISSING_CANDLE"):
            lab.experiment(raw)

    def test_non_overlapping_maturity_and_cost_math(self):
        rows = lab.normalize(candles())
        samples = lab.matured_samples(rows, 200, hold_bars=8)
        n = sum(len(v) for v in samples.values())
        self.assertLessEqual(n, 21)
        self.assertAlmostEqual(lab.side_cost(), .0095)
        self.assertGreater(lab.experiment(candles())["breakeven_gross_roundtrip_pct"], 1.9)

    def test_matured_window_never_crosses_decision_boundary(self):
        rows = lab.normalize(candles())
        index = 250
        before = lab.matured_samples(rows, index)
        mutated = lab.normalize(candles())
        for j in range(index+1, len(mutated)):
            mutated[j]["open"] *= 3
        self.assertEqual(before, lab.matured_samples(mutated, index))

    def test_reject_fictitious_zero_fees_and_nan_gates(self):
        with self.assertRaisesRegex(ValueError, "INVALID_COST"):
            lab.side_cost(float("nan"), 10)
        rows = lab.normalize(candles())
        with self.assertRaisesRegex(ValueError, "INVALID_GATE"):
            lab.forecast(rows, 240, confidence_z=float("nan"))


if __name__ == "__main__":
    unittest.main()
