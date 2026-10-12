"""Live adaptive policy integration tests. All trades and candles are mocked."""
import tempfile
import unittest
from pathlib import Path

from src.bean_growth_adaptive import BAR, BeanAdaptiveGrowthGate, _fresh_state


class GrowthPolicyTests(unittest.TestCase):
    def test_reward_changes_decision_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "growth.sqlite3"
            end = 300 * 500
            bars = {t: {"open": 100 + (t // BAR) * .01,
                        "close": 100 + (t // BAR) * .01 + (.7 if t // BAR % 2 else -.5)}
                    for t in range(end - 280 * BAR, end, BAR)}
            def fetch(market, start, stop):
                self.assertEqual(market, "ETH-USD")
                return {t: row for t, row in bars.items() if start <= t < stop}
            now = [end + 40]
            g = BeanAdaptiveGrowthGate(path=path, fetcher=fetch, clock=lambda: now[0])
            first = g.assess("WETH")
            self.assertEqual(first["status"], "READY")
            self.assertEqual(first["trade_effect"], "ADOPTED_LONG_CASH")
            self.assertGreater(first["reward_updates"], 20)
            self.assertGreater(first["strategy_changes"], 0)
            self.assertGreater(first["paper_trades"], 0)
            self.assertGreater(first["paper_equity"], 0)
            self.assertEqual(first["mode"], "live")
            # No future candles: never invent a second learning update.
            self.assertEqual(g.assess("WETH")["reward_updates"], first["reward_updates"])
            restored = BeanAdaptiveGrowthGate(path=path, fetcher=fetch, clock=lambda: now[0])
            self.assertEqual(restored.assess("WETH")["reward_updates"], first["reward_updates"])
            self.assertEqual(restored.assess("WETH")["learned_strategy"], first["learned_strategy"])
            # A new completed candle drives another real reward update.
            bars[end] = {"open": 101.0, "close": 101.6}
            now[0] += BAR
            updated = restored.assess("WETH")
            self.assertEqual(updated["reward_updates"], first["reward_updates"] + 1)
            self.assertEqual(updated["forward_bars"], 1)

    def test_policy_writes_realized_reward_and_learns_only_later(self):
        s = _fresh_state()
        market = "ETH-USD"
        first = {"open": 100.0, "close": 101.0}
        second = {"open": 102.0, "close": 100.0}
        BeanAdaptiveGrowthGate._apply_bar(s, first, 0, market)
        self.assertEqual(s["paper_bars"], 0)
        prior = s["pending"].copy()
        BeanAdaptiveGrowthGate._apply_bar(s, second, BAR, market)
        self.assertEqual(s["paper_bars"], 1)
        self.assertEqual(s["visits"][prior["context"]][prior["action"]], 1)
        self.assertEqual(s["last_bar"], BAR)
        self.assertEqual(sum(sum(r.values()) for r in s["visits"].values()), 1)

    def test_unavailable_market_does_not_create_reward(self):
        with tempfile.TemporaryDirectory() as temp:
            g = BeanAdaptiveGrowthGate(path=Path(temp) / "db.sqlite",
                fetcher=lambda *_: {}, clock=lambda: BAR * 500)
            response = g.assess("WETH")
            self.assertEqual(response["status"], "DATA_UNAVAILABLE")
            self.assertEqual(response["trade_effect"], "NONE")

    def test_no_duplicate_bars_scored(self):
        s = _fresh_state()
        x = {"open": 100, "close": 101}
        self.assertTrue(BeanAdaptiveGrowthGate._apply_bar(s, x, 0, "ETH-USD"))
        self.assertFalse(BeanAdaptiveGrowthGate._apply_bar(s, x, 0, "ETH-USD"))
        self.assertEqual(s["paper_bars"], 0)


if __name__ == "__main__":
    unittest.main()
