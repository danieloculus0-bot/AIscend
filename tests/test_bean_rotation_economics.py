import unittest

from src.bean_rotation_economics import (
    score_route, bridge_order, maturity_minutes, trade_cost_bps, DEFAULT_TAKER_BPS,
)


class EconomicRotationTests(unittest.TestCase):
    def test_default_route_cost_uses_current_us_taker_fallback(self):
        from src.primary_strategy import DEFAULT_TAKER_BPS as PRIMARY_FALLBACK
        self.assertEqual(DEFAULT_TAKER_BPS, 90.0)
        self.assertEqual(PRIMARY_FALLBACK, DEFAULT_TAKER_BPS)
        route = score_route({"reward_bps": 10.0, "spread_bps": 0.0,
                             "return_5m": 0.01})
        self.assertEqual(route["total_route_cost_bps"], 180.0)
        discounted = score_route({"reward_bps": 10.0, "spread_bps": 0.0,
                                  "return_5m": 0.01}, taker_fee_bps=35)
        self.assertEqual(discounted["total_route_cost_bps"], 70.0)

    def test_quote_bridge_only_when_listed(self):
        products = ["USDC-USD", "ALEO-USD"]
        self.assertEqual(
            bridge_order("USDC", "USD", products),
            {"product": "USDC-USD", "side": "SELL"})
        self.assertEqual(
            bridge_order("USD", "USDC", products),
            {"product": "USDC-USD", "side": "BUY"})
        self.assertIsNone(bridge_order("ETH", "USD", products))
        self.assertIsNone(bridge_order("USDC", "USD", ["ALEO-USD"]))

    def test_expected_return_must_cover_every_leg(self):
        candidate = {"product": "ALEO-USD", "reward_bps": 30.0,
                     "spread_bps": 10.0, "return_5m": 0.01,
                     "return_1h": 0.03, "return_6h": 0.05}
        direct = score_route(candidate, taker_fee_bps=60)
        via_bridge = score_route(candidate, taker_fee_bps=60,
                                 bridge=True, bridge_spread_bps=2.0,
                                 selling_incumbent=True,
                                 incumbent_spread_bps=8.0)
        self.assertEqual(direct["maturity_minutes"], 360)
        self.assertGreater(direct["net_projected_bps"], via_bridge["net_projected_bps"])
        self.assertEqual(via_bridge["total_route_cost_bps"], 255.0)
        self.assertGreater(via_bridge["net_projected_bps"], 0)

    def test_negative_expected_growth_never_becomes_positive_from_cross_trade(self):
        candidate = {"reward_bps": -5.08713, "spread_bps": 2.0}
        plan = score_route(candidate, taker_fee_bps=60, bridge=True)
        self.assertLess(plan["net_projected_bps"], 0)

    def test_horizon_is_provisional_not_fixed_by_profit_target(self):
        self.assertEqual(maturity_minutes({"return_5m": 0.01,
                                           "return_1h": 0.02,
                                           "return_6h": 0.03}), 360)
        self.assertEqual(maturity_minutes({"return_5m": 0.01}), 15)
        self.assertEqual(maturity_minutes({}), 5)
        self.assertEqual(trade_cost_bps(12, 60), 66)


if __name__ == "__main__":
    unittest.main()
