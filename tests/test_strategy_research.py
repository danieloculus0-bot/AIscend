import unittest
from inspect import signature
from unittest.mock import patch

from src.bean_five_minute import BAR, fetch_5m
from src.strategy_research import validate, simulate, RESEARCH_POLICIES
from src.primary_strategy import POLICIES


def fixture(n=1100, *, trend=.001):
    ts0=1_700_000_100 // BAR * BAR
    data={}
    price=10.0
    for k in range(n):
        # Deterministic, future-independent example, not market evidence.
        op=price
        price=price*(1+trend)
        data[ts0+k*BAR]={"open":op,"close":price,
                         "volume":400.0 if k%6 == 0 else 100.0}
    return data


class ResearchEvidenceTests(unittest.TestCase):
    def test_default_fee_model_is_current_us_entry_taker_baseline(self):
        self.assertEqual(signature(simulate).parameters["fee_bps"].default, 90.0)
        self.assertEqual(signature(validate).parameters["fee_bps"].default, 90.0)

    def test_all_policies_evaluated_with_chronological_holdout(self):
        result=validate(fixture())
        self.assertIn(result["champion"],RESEARCH_POLICIES)
        self.assertEqual(set(result["training"]),set(RESEARCH_POLICIES))
        self.assertEqual(set(result["holdout"]),set(RESEARCH_POLICIES))
        self.assertIn(result["status"],("OOS_PASS","OOS_NOT_PROVEN"))
        self.assertIsInstance(result["holdout_buy_hold_net"],float)

    def test_training_and_champion_cannot_read_changed_future(self):
        bars=fixture()
        earlier=validate(bars)
        keys=sorted(bars)
        for key in keys[850:]:
            bars[key]["open"] *= .7
            bars[key]["close"] *= .7
        later=validate(bars)
        self.assertEqual(earlier["training"],later["training"])
        self.assertEqual(earlier["champion"],later["champion"])

    def test_net_costs_decrease_returns(self):
        bars=fixture()
        cheap=simulate(bars,"trend",fee_bps=1)
        expensive=simulate(bars,"trend",fee_bps=100)
        self.assertGreater(cheap["trades"],0)
        self.assertEqual(cheap["trades"],expensive["trades"])
        self.assertGreater(cheap["net_return"],expensive["net_return"])

    def test_no_trade_baseline_never_fake_profits(self):
        cash=simulate(fixture(),"cash")
        self.assertEqual(cash["net_return"],0.0)
        self.assertEqual(cash["trades"],0)

    def test_one_day_hold_has_fewer_fee_events_than_breakout(self):
        bars=fixture()
        daily=simulate(bars,"trend_1d_hold",fee_bps=60)
        breakout=simulate(bars,"breakout",fee_bps=60)
        self.assertLess(daily["trades"],breakout["trades"])

    def test_short_history_makes_no_success_claim(self):
        result=validate(fixture(100))
        self.assertEqual(result["status"],"INSUFFICIENT_HISTORY")
        self.assertNotIn("promotion_eligible",result)

    def test_flat_market_volume_does_not_confirm_breakout(self):
        bars=fixture()
        self.assertGreater(simulate(bars,"breakout")["trades"],0)
        for candle in bars.values():
            candle["volume"]=100.0
        self.assertEqual(simulate(bars,"breakout")["trades"],0)

    def test_missing_and_zero_market_volume_do_not_confirm_breakout(self):
        bars=fixture()
        for candle in bars.values():
            candle.pop("volume")
        self.assertEqual(simulate(bars,"breakout")["trades"],0)
        for candle in bars.values():
            candle["volume"]=0.0
        self.assertEqual(simulate(bars,"breakout")["trades"],0)

    def test_interim_underwater_position_increases_drawdown(self):
        bars=fixture()
        baseline=simulate(bars,"trend")
        keys=sorted(bars)
        # An adverse interim close is followed by a full recovery before
        # scheduled liquidation, so final trade P/L should not conceal risk.
        bars[keys[80]]["close"]*=0.35
        stressed=simulate(bars,"trend")
        self.assertAlmostEqual(baseline["net_return"],
                               stressed["net_return"],places=6)
        self.assertGreater(stressed["max_drawdown"],0.50)
        self.assertGreater(stressed["max_drawdown"],baseline["max_drawdown"])

    def test_missing_holding_period_candles_invalidate_crossing_trades(self):
        baseline = fixture(n=1100)
        observed = simulate(baseline, "trend")
        self.assertGreater(observed["trades"], 0)
        keys = sorted(baseline)
        discontinuity = keys[105]
        # A genuine 5-minute outage occurs AFTER the entry's complete
        # lookback but BEFORE its planned exit, and must cancel that trade.
        bars = {
            (ts if i <= 105 else ts + BAR): baseline[ts]
            for i, ts in enumerate(keys)
        }
        result = simulate(bars, "trend")
        self.assertTrue(all(not (
            trade["entry_ts"] <= discontinuity
            and trade["exit_ts"] >= discontinuity + 2*BAR
        ) for trade in result["trade_examples"]))
        self.assertTrue(any(
            trade["entry_ts"] <= discontinuity
            and trade["exit_ts"] > discontinuity
            for trade in observed["trade_examples"]))

    def test_coinbase_loader_retains_actual_volume(self):
        ts=1_700_000_100 // BAR * BAR
        response=[[ts, 9.0, 11.0, 10.0, 10.5, 123.45]]
        with patch("src.bean_five_minute._get_json",return_value=response):
            bars=fetch_5m("BTC-USD",ts,ts+BAR)
        self.assertEqual(bars[ts]["volume"],123.45)

    def test_coinbase_invalid_volume_never_becomes_fake_confirmation(self):
        ts=1_700_000_100 // BAR * BAR
        response=[[ts, 9.0, 11.0, 10.0, 10.5, None]]
        with patch("src.bean_five_minute._get_json",return_value=response):
            bars=fetch_5m("BTC-USD",ts,ts+BAR)
        self.assertNotIn("volume",bars[ts])


if __name__=="__main__":
    unittest.main()
