import unittest

from src.bean_five_minute import BAR
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
        data[ts0+k*BAR]={"open":op,"close":price}
    return data


class ResearchEvidenceTests(unittest.TestCase):
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


if __name__=="__main__":
    unittest.main()
