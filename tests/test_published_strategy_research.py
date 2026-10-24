"""Offline tests of published systematic crypto rule adaptations."""
import unittest
from src.published_strategy_research import (
    BAR, RULES, _signal, evaluate, fetch_public_ohlcv,
    normalize, simulate, WARMUP,
)


def series(n=900, *, slope=1., gap=None):
    out = {}
    for i in range(n):
        if i == gap:
            continue
        price = 100. + slope*i
        out[i*BAR] = {"ts": i*BAR, "open": price,
                      "high": price+.1, "low": price-.1,
                      "close": price, "volume": 100.}
    return out


class PublishedRuleResearchTests(unittest.TestCase):
    def test_original_20_and_55_day_donchian_variants(self):
        rows = normalize(series())
        self.assertTrue(_signal(rows, 300, ("donchian_20_10",))[0])
        self.assertTrue(_signal(rows, 300, ("donchian_55_20",))[0])
        rows[299]["high"] = 999999
        self.assertFalse(_signal(rows, 300, ("donchian_20_10",))[0])
        self.assertFalse(_signal(rows, 300, ("donchian_55_20",))[0])

    def test_donchian_exit_is_prior_window_low(self):
        rows = normalize(series())
        rows[300]["close"] = 1.
        rows[300]["low"] = 0.9
        rows[300]["open"] = 1.
        rows[300]["high"] = 1.1
        self.assertTrue(_signal(rows, 300, ("donchian_20_10",))[1])
        self.assertTrue(_signal(rows, 300, ("donchian_55_20",))[1])

    def test_ma_band_momentum_and_obv(self):
        rows = normalize(series())
        self.assertTrue(_signal(rows, 300, ("sma_band_10_30", .02))[0])
        self.assertTrue(_signal(rows, 300,
                                ("time_series_momentum_30d", .04))[0])
        self.assertTrue(_signal(rows, 300,
                                ("time_series_momentum_60d", .08))[0])
        self.assertTrue(_signal(rows, 300, ("obv_trend",))[0])

    def test_60_day_momentum_warmup_never_reads_future_row(self):
        rows = normalize(series())
        rows[-1]["close"] = 999999.
        self.assertEqual(
            _signal(rows, WARMUP-1, ("time_series_momentum_60d", .08)),
            (False, False))
        self.assertEqual(
            _signal(rows, WARMUP-1, ("donchian_20_10",)),
            (False, False))

    def test_trade_buys_at_next_open_not_signal_close(self):
        rows = normalize(series())
        opening = 700.
        closing = 770.
        rows[300].update(open=opening, high=closing+.1,
                         low=opening-.1, close=closing)
        trade = simulate(rows, ("donchian_20_10",), start=300, end=301)
        cost = (90+10/2)/10000
        expected = (1-cost)**2*closing/opening-1
        self.assertAlmostEqual(trade["net_return"], expected, places=7)
        self.assertTrue(trade["open_position"])

    def test_higher_fees_cannot_improve_same_trade(self):
        rows = normalize(series())
        small = simulate(rows, ("donchian_20_10",),
                         start=300, end=304, fee_bps=30)
        large = simulate(rows, ("donchian_20_10",),
                         start=300, end=304, fee_bps=180)
        self.assertLess(large["net_return"], small["net_return"])

    def test_partial_candle_gap_is_expected_status(self):
        r = evaluate(series(gap=350))
        self.assertEqual(r["status"], "PARTIAL")
        self.assertFalse(r["promotion_authorized"])

    def test_insufficient_history_is_expected_status(self):
        r = evaluate(series(n=650))
        self.assertEqual(r["status"], "INSUFFICIENT_HISTORY")
        self.assertFalse(r["promotion_authorized"])

    def test_holdout_does_not_change_training_selection(self):
        base = series()
        modified = {t: dict(v) for t, v in base.items()}
        for i in range(int(len(base)*.70), len(base)):
            price = 10000. - i
            modified[i*BAR].update(open=price, high=price+.1,
                                   low=price-.1, close=price)
        first, second = evaluate(base), evaluate(modified)
        self.assertEqual(first["training"], second["training"])
        self.assertEqual(first["selected_rule"], second["selected_rule"])
        self.assertNotEqual(first["buy_hold_net"], second["buy_hold_net"])
        self.assertFalse(first["promotion_authorized"])

    def test_no_hypothetical_cash_data_promoted(self):
        result = evaluate(series())
        self.assertIn(result["status"], {"OOS_NOT_PROVEN", "OOS_PASS"})
        self.assertEqual(len(result["training"]), len(RULES))
        self.assertFalse(result["promotion_authorized"])
        self.assertLess(result["training_end"], result["holdout_start"])

    def test_public_candle_fetch_preserves_true_high_low(self):
        end = 175*86400
        candles = [[i*BAR, 99., 103., 100., 102., 22.]
                   for i in range(175*4)]
        urls = []
        def fetcher(url):
            urls.append(url)
            self.assertIn("/BTC-USD/candles?", url)
            return candles
        rows = fetch_public_ohlcv("BTC-USD", days=175,
                                  clock=lambda: end, fetcher=fetcher)
        self.assertEqual(len(rows), 175*4)
        self.assertEqual(rows[0]["low"], 99.)
        self.assertEqual(rows[0]["high"], 103.)
        self.assertGreaterEqual(len(urls), 3)

    def test_opening_future_bar_cannot_change_prior_signal(self):
        rows = normalize(series())
        prior = _signal(rows, 299, ("donchian_20_10",))
        rows[300].update(open=9999., high=10001.,
                         low=9998., close=10000.)
        self.assertEqual(prior, _signal(rows, 299, ("donchian_20_10",)))

    def test_reject_infinite_cost(self):
        with self.assertRaisesRegex(ValueError, "INVALID_COST"):
            simulate(normalize(series()), ("donchian_20_10",),
                     start=WARMUP, end=400, fee_bps=float("inf"))


if __name__ == "__main__":
    unittest.main()
