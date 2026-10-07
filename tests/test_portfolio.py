import tempfile
import unittest
from pathlib import Path

from src.core import StateStore
from src.portfolio import choose_execution_rail, combine_portfolio


class PortfolioCoordinatorTests(unittest.TestCase):
    def test_split_rails_are_one_25_dollar_bankroll(self):
        base = {
            "cash": 10.0,
            "net_liquidation": 10.0,
            "market_value": 0.0,
            "positions": {},
            "prices": {},
            "research": {"available": False, "human": {"available": True}},
            "bean": {"claims": 12},
        }
        advanced = {
            "cash": 15.0,
            "net_liquidation": 15.0,
            "market_value": 0.0,
            "positions": {},
            "prices": {},
            "research": {"available": True, "composite_score": 0.0},
            "market_radar": {},
        }

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "portfolio.db")
            try:
                store.set_meta("live_starting_value", "25.0")
                snap = combine_portfolio(
                    base=base,
                    advanced=advanced,
                    active_rail="advanced",
                    game_store=store,
                )
            finally:
                store.close()

        self.assertEqual(snap["net_liquidation"], 25.0)
        self.assertEqual(snap["pnl"], 0.0)
        self.assertEqual(snap["multiple"], 1.0)
        self.assertEqual(snap["portfolio"]["base_net"], 10.0)
        self.assertEqual(snap["portfolio"]["advanced_net"], 15.0)
        self.assertTrue(snap["portfolio"]["complete"])

    def test_coordinator_chooses_funded_advanced_trade(self):
        base = {
            "cash": 0.0,
            "net_liquidation": 0.0,
            "positions": {},
            "research": {
                "available": True,
                "composite_score": -0.02,
                "confidence": 0.02,
                "human": {"fomo_index": 35, "crowd_regime": "CAUTIOUS"},
                "eth": {"return_5m": 0, "return_1h": 0, "volume_ratio": 1},
                "thesis": "neutral",
            },
        }
        advanced = {
            "cash": 25.0,
            "net_liquidation": 25.0,
            "cash_by_quote": {"USDC": 25.0},
            "positions": {},
            "market_radar": {
                "best_long": {
                    "product": "ALEO-USDC",
                    "decision_score": 0.42,
                    "score": 0.42,
                    "spread_bps": 8.0,
                    "volume_ratio": 1.4,
                }
            },
        }
        self.assertEqual(choose_execution_rail(base, advanced), "advanced")

    def test_coordinator_journals_hold_on_funded_rail(self):
        base = {
            "cash": 0.0,
            "net_liquidation": 0.0,
            "positions": {},
            "research": {"available": False},
        }
        advanced = {
            "cash": 25.0,
            "net_liquidation": 25.0,
            "cash_by_quote": {"USDC": 25.0},
            "positions": {},
            "market_radar": {},
        }
        self.assertEqual(choose_execution_rail(base, advanced), "advanced")


if __name__ == "__main__":
    unittest.main()
