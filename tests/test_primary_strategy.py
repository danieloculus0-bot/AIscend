"""Primary AIscend trading policy tests: price action and quote economics."""
import unittest

from src.primary_strategy import proposals, select_plan, exit_reason


def row(p, *, r5=.012, r1=.04, r6=.08, volume=2.0, spread=5):
    return {"product":p, "return_5m":r5, "return_1h":r1,
            "return_6h":r6, "volume_ratio":volume, "spread_bps":spread}


class PrimaryPolicyTests(unittest.TestCase):
    def test_common_strategy_set_is_independent_of_bean(self):
        x=proposals(row("ALEO-USD"))
        self.assertEqual({i["strategy"] for i in x},{"trend","breakout"})
        self.assertTrue(all(i["gross_projection_bps"]>0 for i in x))

    def test_tradable_cross_quote_position_can_fund_opportunity(self):
        snap={"asset_universe":{"scored":[row("ALEO-USD"),
                                             row("GROVE-USDC",r5=-.02,r1=-.05,r6=-.1)]},
              "tradable_products":["ALEO-USD","GROVE-USDC","USDC-USD"],
              "cash_by_quote":{"USDC":.88276659},
              "positions":{"GROVE-USDC":{"qty":10,"available_qty":10}},
              "balance_values_usd":{"GROVE":20.0}}
        p=select_plan(snap,taker_bps=60)
        self.assertEqual(p["winner"]["product"],"ALEO-USD")
        self.assertEqual(p["winner"]["source_asset"],"GROVE-USDC")
        self.assertTrue(p["winner"]["conversion_required"])
        self.assertGreater(p["winner"]["net_projected_bps"],0)

    def test_no_bridge_is_explained(self):
        snap={"asset_universe":{"scored":[row("ALEO-USD")]},
              "tradable_products":["ALEO-USD"],
              "cash_by_quote":{"USDC":25.0},"positions":{}}
        p=select_plan(snap,taker_bps=60)
        self.assertIsNone(p["winner"])
        self.assertGreater(p["skipped"]["no_quote_path"],0)

    def test_spread_and_fees_cannot_be_ignored(self):
        snap={"asset_universe":{"scored":[row("ALEO-USDC",r5=.002,
                                        r1=.004,r6=.001,volume=1.4,spread=500)]},
              "tradable_products":["ALEO-USDC"],
              "cash_by_quote":{"USDC":25.0},"positions":{}}
        p=select_plan(snap,taker_bps=60)
        self.assertIsNone(p["winner"])
        self.assertGreater(p["skipped"]["negative_net_edge"],0)

    def test_held_assets_are_not_double_bought(self):
        snap={"asset_universe":{"scored":[row("GROVE-USDC")]},
              "tradable_products":["GROVE-USDC"],
              "cash_by_quote":{"USDC":10.0},
              "positions":{"GROVE-USDC":{"qty":1}}}
        self.assertIsNone(select_plan(snap)["winner"])

    def test_negative_trend_invalidates_existing_holding(self):
        snap={"asset_universe":{"scored":[row("GROVE-USDC",r5=-.02,r1=-.05,r6=-.1)]}}
        self.assertEqual(exit_reason("GROVE-USDC",snap),"trend_invalidation")


if __name__=="__main__":
    unittest.main()
