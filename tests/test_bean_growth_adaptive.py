"""Live adaptive policy integration tests. All trades and candles are mocked."""
import tempfile
import unittest
from pathlib import Path

from src.bean_growth_adaptive import (BAR, BeanAdaptiveGrowthGate,
    _fresh_state, _features, _choose, _position, MODEL_FEE)
from src.primary_strategy import DEFAULT_TAKER_BPS


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

    def test_missing_bar_does_not_preserve_imaginary_held_equity(self):
        state = _fresh_state()
        state["last_bar"] = 0
        state["last_close"] = 100.0
        state["held"] = 1
        state["pending"] = {"context": "initial", "action": "cash",
                            "position": 0}
        # The missing bar could have contained a SELL fill. No opening price
        # or realized paper P/L can be inferred from the later candle.
        with self.assertRaisesRegex(ValueError, "noncontiguous"):
            BeanAdaptiveGrowthGate._apply_bar(
                state, {"open": 50.0, "close": 50.0}, 3 * BAR, "ETH-USD")
        self.assertEqual(state["last_bar"], 0)
        self.assertEqual(state["equity"], 100.0)
        self.assertEqual(state["paper_bars"], 0)

    def test_short_contiguous_suffix_blocks_adoption(self):
        end = 500 * BAR
        bars = {t: {"open": 100.0, "close": 100.0}
                for t in range(end - 280 * BAR, end, BAR)}
        del bars[end - 15 * BAR]
        with tempfile.TemporaryDirectory() as temp:
            gate = BeanAdaptiveGrowthGate(
                path=Path(temp) / "growth.sqlite3",
                fetcher=lambda _p, a, b: {
                    t: v for t, v in bars.items() if a <= t < b},
                clock=lambda: end + 10)
            report = gate.assess("ETH-USD")
            self.assertEqual(report["status"], "DATA_UNAVAILABLE")
            self.assertEqual(report["trade_effect"], "NONE")

    def test_offline_gap_restarts_equity_but_retains_q_learning(self):
        end = 500 * BAR
        bars = {t: {"open": 100.0, "close": 100.0}
                for t in range(end - 280 * BAR, end, BAR)}
        now = [end + 10]
        with tempfile.TemporaryDirectory() as temp:
            gate = BeanAdaptiveGrowthGate(
                path=Path(temp) / "growth.sqlite3",
                fetcher=lambda _p, a, b: {
                    t: v for t, v in bars.items() if a <= t < b},
                clock=lambda: now[0])
            before = gate.assess("ETH-USD")
            self.assertEqual(before["status"], "READY")
            prior_updates = before["reward_updates"]
            new_end = end + 100 * BAR
            # Five unavailable bars include the unknowable scheduled fill.
            for t in range(end + 5 * BAR, new_end, BAR):
                bars[t] = {"open": 50.0, "close": 50.0}
            now[0] = new_end + 10
            after = gate.assess("ETH-USD")
            self.assertEqual(after["status"], "READY")
            self.assertEqual(after["equity_segment_restarts"], 1)
            self.assertGreater(after["reward_updates"], prior_updates)
            self.assertLessEqual(after["paper_equity"], 100.0)
            with gate._db() as db:
                archive = db.execute(
                    "SELECT state FROM policy_archive WHERE product=? AND "
                    "model_version=?", ("ETH-USD",
                    "history_gap_after_" + str(end - BAR))).fetchone()
            self.assertIsNotNone(archive)

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

    def test_negative_realized_reward_changes_preferred_action_and_position(self):
        # No canned 'learned action': execute a real simulated long, grade
        # its unfavorable next candle, then compare the same market context.
        state = _fresh_state()
        state["bootstrapped"] = True
        state["recent_returns"] = [.01] * 12
        state["last_close"] = 100.0
        state["last_bar"] = BAR * 98
        context = _features(state["recent_returns"])
        before = _choose(state, context, explore=False, seed=0)
        self.assertEqual(before, "momentum")
        self.assertEqual(_position(before, state["recent_returns"]), 1)
        state["pending"] = {"context": context, "action": before, "position": 1}
        # Price closes ABOVE previous close (same context), but buying at
        # the much-higher next open loses money after cost.
        BeanAdaptiveGrowthGate._apply_bar(
            state, {"open": 105.0, "close": 101.0}, BAR * 99, "ETH-USD")
        current = _features(state["recent_returns"])
        self.assertEqual(current, context)
        after = _choose(state, current, explore=False, seed=0)
        self.assertNotEqual(after, before)
        self.assertEqual(_position(after, state["recent_returns"]), 0)
        self.assertLess(state["q"][context]["momentum"], 0)
        self.assertEqual(state["behavior_revisions"], 1)
        self.assertEqual(state["forward_behavior_revisions"], 1)
        self.assertEqual(state["last_behavior_revision"]["before"], before)
        self.assertEqual(state["last_behavior_revision"]["after"], after)

    def test_continuing_long_gap_updates_q_reward_as_well_as_equity(self):
        # A long held across the bar boundary owns the gap. The Q learner
        # must not report a zero reward while paper capital loses 10%.
        for opening, gap_bps in ((90.0, -1000.0), (110.0, 1000.0)):
            s = _fresh_state()
            s["last_bar"] = 0
            s["last_close"] = 100.0
            s["held"] = 1
            s["pending"] = {"context": "initial", "action": "buy_hold",
                            "position": 1}
            BeanAdaptiveGrowthGate._apply_bar(
                s, {"open": opening, "close": opening}, BAR, "ETH-USD")
            self.assertAlmostEqual(s["equity"], opening, places=8)
            self.assertAlmostEqual(
                s["q"]["initial"]["buy_hold"], .08 * gap_bps, places=8)
            self.assertEqual(s["win_trials"]["initial"]["buy_hold"], 1)
            self.assertEqual(
                s["wins"].get("initial", {}).get("buy_hold", 0),
                int(gap_bps > 0))

    def test_cash_exit_not_penalized_for_gap_from_prior_long(self):
        # A sell at the next open cannot undo the gap already suffered by
        # the old position. Charge the cash action its SELL fee, not the gap.
        s = _fresh_state()
        s["last_bar"] = 0
        s["last_close"] = 100.0
        s["held"] = 1
        s["pending"] = {"context": "initial", "action": "cash",
                        "position": 0}
        BeanAdaptiveGrowthGate._apply_bar(
            s, {"open": 90.0, "close": 90.0}, BAR, "ETH-USD")
        self.assertAlmostEqual(
            s["equity"], 90.0 * (1 - MODEL_FEE), places=8)
        self.assertAlmostEqual(
            s["q"]["initial"]["cash"], -.08 * MODEL_FEE * 10000, places=8)

    def test_pre_gap_reward_model_is_archived_before_relearning(self):
        import json

        end = 500 * BAR
        bars = {ts: {"open": 100.0, "close": 100.0}
                for ts in range(end - 280 * BAR, end, BAR)}
        with tempfile.TemporaryDirectory() as temp:
            gate = BeanAdaptiveGrowthGate(
                path=Path(temp) / "policy.sqlite3",
                fetcher=lambda _p, a, b: {
                    t: v for t, v in bars.items() if a <= t < b},
                clock=lambda: end + 10)
            stale = _fresh_state()
            stale["cost_model"] = "taker60bps_held_gap_v2"
            stale["q"] = {"initial": {"buy_hold": 98765.0}}
            with gate._db() as db:
                db.execute("INSERT INTO policy(product,state) VALUES(?,?)",
                           ("ETH-USD", json.dumps(stale)))
            self.assertEqual(gate.assess("ETH-USD")["status"], "READY")
            with gate._db() as db:
                archived = db.execute(
                    "SELECT state FROM policy_archive WHERE product=? AND "
                    "model_version=?", ("ETH-USD",
                    "taker60bps_held_gap_v2")).fetchone()
                updated = db.execute(
                    "SELECT state FROM policy WHERE product=?",
                    ("ETH-USD",)).fetchone()
            self.assertIsNotNone(archived)
            self.assertEqual(
                json.loads(archived["state"])["q"]["initial"]["buy_hold"],
                98765.0)
            self.assertNotEqual(
                json.loads(updated["state"])["q"].get("initial", {}).get("buy_hold"),
                98765.0)

    def test_mock_round_trip_fee_matches_live_order_cost_assumption(self):
        # The live primary engine starts with a 60 bps taker-cost estimate.
        # Seven-basis-point simulated transitions fabricate cheap turnover.
        self.assertAlmostEqual(MODEL_FEE, DEFAULT_TAKER_BPS / 10000)
        s = _fresh_state()
        s["last_bar"] = 0
        s["last_close"] = 100.0
        s["pending"] = {"context": "initial", "action": "buy_hold", "position": 1}
        BeanAdaptiveGrowthGate._apply_bar(
            s, {"open": 100.0, "close": 100.0}, BAR, "ETH-USD")
        self.assertAlmostEqual(s["equity"], 100.0 * (1 - MODEL_FEE), places=8)
        self.assertEqual(s["mock_trades"], 1)
        s["pending"] = {"context": "initial", "action": "cash", "position": 0}
        BeanAdaptiveGrowthGate._apply_bar(
            s, {"open": 100.0, "close": 100.0}, BAR * 2, "ETH-USD")
        self.assertAlmostEqual(s["equity"], 100.0 * (1 - MODEL_FEE) ** 2, places=8)
        self.assertEqual(s["mock_trades"], 2)

    def test_held_asset_price_gap_is_marked_before_selling_at_next_open(self):
        # A real long position loses money when the next candle opens 10% lower,
        # even if a sell signal then exits immediately at that open.
        s = _fresh_state()
        s["last_bar"] = 0
        s["last_close"] = 100.0
        s["held"] = 1
        s["pending"] = {"context": "initial", "action": "cash", "position": 0}
        BeanAdaptiveGrowthGate._apply_bar(
            s, {"open": 90.0, "close": 90.0}, BAR, "ETH-USD")
        self.assertAlmostEqual(
            s["equity"], 100 * .90 * (1 - MODEL_FEE), places=8)
        self.assertLess(s["equity"], 90)
        self.assertEqual(s["held"], 0)

    def test_cash_does_not_gain_from_price_gap_prior_to_buy(self):
        s = _fresh_state()
        s["last_bar"] = 0
        s["last_close"] = 100.0
        s["held"] = 0
        s["pending"] = {"context": "initial", "action": "buy_hold", "position": 1}
        BeanAdaptiveGrowthGate._apply_bar(
            s, {"open": 110.0, "close": 110.0}, BAR, "ETH-USD")
        self.assertAlmostEqual(s["equity"], 100 * (1 - MODEL_FEE), places=8)

    def test_legacy_low_fee_q_scores_are_archived_and_recalibrated(self):
        import json

        end = BAR * 500
        candles = {
            ts: {"open": 100.0, "close": 100.0}
            for ts in range(end - BAR * 280, end, BAR)
        }
        with tempfile.TemporaryDirectory() as folder:
            gate = BeanAdaptiveGrowthGate(
                path=Path(folder) / "policy.sqlite3",
                fetcher=lambda _product, start, stop: {
                    ts: bar for ts, bar in candles.items() if start <= ts < stop
                },
                clock=lambda: end + 20)
            stale = _fresh_state()
            stale.pop("cost_model", None)
            stale["schema"] = "aiscend.bean.growth.v1"
            stale["q"] = {"initial": {"buy_hold": 9999.0}}
            with gate._db() as db:
                db.execute("INSERT INTO policy(product,state) VALUES(?,?)",
                           ("ETH-USD", json.dumps(stale)))
            result = gate.assess("ETH-USD")
            self.assertEqual(result["status"], "READY")
            self.assertEqual(result["paper_taker_fee_bps"], DEFAULT_TAKER_BPS)
            with gate._db() as db:
                archive = db.execute(
                    "SELECT model_version,state FROM policy_archive WHERE product=?",
                    ("ETH-USD",)).fetchone()
                updated = db.execute(
                    "SELECT state FROM policy WHERE product=?",
                    ("ETH-USD",)).fetchone()
            self.assertEqual(archive["model_version"], "legacy_fee_7bps")
            self.assertEqual(json.loads(archive["state"])["q"]["initial"]["buy_hold"], 9999.0)
            self.assertNotEqual(
                json.loads(updated["state"])["q"].get("initial", {}).get("buy_hold"),
                9999.0)

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
