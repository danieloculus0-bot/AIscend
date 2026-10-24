"""Offline causal, fee-net strategy discovery regressions."""
import unittest
from src.strategy_discovery import BAR, WARMUP, CONFIGS, candles, signal, simulate, challenge


def history(n=360, fn=None, volume=None, gap=None):
    fn = fn or (lambda i: 100 + i*.3)
    return {i*BAR: {"ts": i*BAR, "open": fn(i), "close": fn(i),
                    "volume": volume(i) if volume else 100}
            for i in range(n) if i != gap}


class StrategyDiscoveryTests(unittest.TestCase):
    def test_volume_breakout_signal(self):
        rows = candles(history(100, fn=lambda i: 120 if i == 50 else 100,
                               volume=lambda i: 500 if i == 50 else 100))
        self.assertTrue(signal(rows, None, 50, ("breakout", 20, 16))[0])
        rows[50]["volume"] = 0
        self.assertFalse(signal(rows, None, 50, ("breakout", 20, 16))[0])

    def test_relative_strength_requires_real_btc(self):
        rows = candles(history(140, fn=lambda i: 100*1.003**i))
        btc = candles(history(140, fn=lambda i: 100*1.0002**i))
        self.assertFalse(signal(rows, None, 100, ("relative_momentum", 28, 16))[0])
        self.assertTrue(signal(rows, btc, 100, ("relative_momentum", 28, 16))[0])

    def test_next_open_gap_not_credited(self):
        rows = candles(history(100, fn=lambda i: 100.))
        rows[49].update(close=115, volume=600)
        rows[50].update(open=130, close=130)
        rows[51].update(open=130, close=130)
        r = simulate(rows, ("breakout", 20, 16), start=50, end=52)
        self.assertTrue(r["open_position"])
        self.assertAlmostEqual(r["net_return"], (1-.0095)**2-1, places=7)

    def test_roundtrip_fees_and_drawdown(self):
        rows = candles(history(110, fn=lambda i: 100.))
        rows[49].update(close=115, volume=600)
        r = simulate(rows, ("breakout", 20, 16), start=50, end=70)
        self.assertLess(r["net_return"], 0)
        self.assertGreaterEqual(r["completed_roundtrips"], 1)
        self.assertGreater(r["max_drawdown"], 0)

    def test_gap_is_normal_partial(self):
        result = challenge(history(gap=160))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse(result["promotion_authorized"])

    def test_short_history_is_normal_insufficient(self):
        r = challenge(history(200))
        self.assertEqual(r["status"], "INSUFFICIENT_HISTORY")
        self.assertFalse(r["promotion_authorized"])

    def test_holdout_not_promotion_and_cost_stress(self):
        r = challenge(history(360, fn=lambda i: 100*1.002**i),
                      btc_raw=history(360, fn=lambda i: 100*1.0002**i))
        self.assertEqual(len(r["training_candidates"]), len(CONFIGS))
        self.assertFalse(r["promotion_authorized"])
        self.assertIn(r["status"], {"OOS_PASS", "OOS_NOT_PROVEN"})
        if r["selected_config"]:
            self.assertIsNotNone(r["stress_holdout"])
            self.assertGreater(r["holdout_start_ts"], r["training_end_ts"])

    def test_absent_btc_disables_relative_momentum(self):
        r = challenge(history())
        self.assertTrue(all(x["config"][0] != "relative_momentum"
                            for x in r["training_candidates"]))

    def test_invalid_cost(self):
        with self.assertRaisesRegex(ValueError, "INVALID_COST"):
            simulate(candles(history(100)), ("breakout", 20, 16),
                     start=WARMUP, end=80, fee_bps=float("nan"))


if __name__ == "__main__":
    unittest.main()
