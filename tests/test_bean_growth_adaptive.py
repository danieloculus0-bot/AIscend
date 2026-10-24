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
            self.assertEqual(first["status"], "WARMING_UP")
            self.assertEqual(first["trade_effect"], "NONE")
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

    def test_selected_policy_requires_its_own_context_samples(self):
        import json

        end = 500 * BAR
        bars = {t: {"open": 100.0, "close": 100.0}
                for t in range(end - 280 * BAR, end, BAR)}
        context = _features([.01] * 12)
        with tempfile.TemporaryDirectory() as temp:
            gate = BeanAdaptiveGrowthGate(
                path=Path(temp) / "policy.sqlite3",
                fetcher=lambda _p, a, b: {
                    t: v for t, v in bars.items() if a <= t < b},
                clock=lambda: end + 10)
            state = _fresh_state()
            state["last_bar"] = end - BAR
            state["last_close"] = 100.0
            state["recent_returns"] = [.01] * 12
            state["bootstrapped"] = True
            state["paper_bars"] = 200
            state["q"] = {context: {"buy_hold": 50.0}}
            state["visits"] = {context: {"buy_hold": 5}}
            state["win_trials"] = {context: {"buy_hold": 5}}
            state["forward_win_trials"] = {context: {"buy_hold": 5}}
            with gate._db() as db:
                db.execute("INSERT INTO policy(product,state) VALUES(?,?)",
                           ("ETH-USD", json.dumps(state)))
            first = gate.assess("ETH-USD")
            self.assertEqual(first["learned_strategy"], "buy_hold")
            self.assertEqual(first["observed_samples"], 5)
            self.assertEqual(first["status"], "WARMING_UP")
            self.assertEqual(first["trade_effect"], "NONE")
            self.assertEqual(first["reward_updates"], 5)
            state["win_trials"][context]["buy_hold"] = 6
            state["forward_win_trials"][context]["buy_hold"] = 6
            state["visits"][context]["buy_hold"] = 6
            with gate._db() as db:
                db.execute("UPDATE policy SET state=? WHERE product=?",
                           (json.dumps(state), "ETH-USD"))
            second = gate.assess("ETH-USD")
            self.assertEqual(second["status"], "READY")
            self.assertEqual(second["trade_effect"], "ADOPTED_LONG_CASH")
            self.assertEqual(second["observed_samples"], 6)

    def test_historical_bootstrap_cannot_immediately_authorize_live_adoption(self):
        import json
        end = 500 * BAR
        candles = {t: {"open": 100.0, "close": 100.0}
                   for t in range(end - 280 * BAR, end, BAR)}
        with tempfile.TemporaryDirectory() as folder:
            gate = BeanAdaptiveGrowthGate(
                path=Path(folder) / "growth.sqlite3",
                fetcher=lambda _p, a, b: {
                    t: row for t, row in candles.items() if a <= t < b},
                clock=lambda: end + 10)
            first = gate.assess("ETH-USD")
            self.assertGreater(first["reward_updates"], 100)
            self.assertEqual(first["forward_bars"], 0)
            self.assertEqual(first["observed_forward_samples"], 0)
            self.assertEqual(first["status"], "WARMING_UP")
            self.assertEqual(first["trade_effect"], "NONE")
            dashboard = gate.status()["markets"][0]
            self.assertEqual(dashboard["adoption_status"], "WARMING_UP")
            self.assertEqual(dashboard["selected_action_forward_samples"], 0)
            self.assertEqual(dashboard["paper_model"],
                             "taker90bps_forward_context_reward_v5")
            # Even an inflated retrospective visit counter cannot satisfy
            # the missing independently observed forward samples.
            with gate._db() as db:
                state = json.loads(db.execute(
                    "SELECT state FROM policy WHERE product=?",
                    ("ETH-USD",)).fetchone()["state"])
                context = _features(state["recent_returns"])
                policy = _choose(state, context, explore=False, seed=0)
                state["win_trials"].setdefault(context, {})[policy] = 300
                db.execute("UPDATE policy SET state=? WHERE product=?",
                           (json.dumps(state), "ETH-USD"))
            later = gate.assess("ETH-USD")
            self.assertEqual(later["status"], "WARMING_UP")
            self.assertEqual(later["observed_forward_samples"], 0)

    def test_forward_reward_samples_are_observed_once_after_bootstrap(self):
        state = _fresh_state()
        state["last_bar"] = 0
        state["last_close"] = 100.0
        state["pending"] = {"context": "initial", "action": "buy_hold",
                            "position": 1}
        BeanAdaptiveGrowthGate._apply_bar(
            state, {"open": 100.0, "close": 101.0}, BAR, "ETH-USD")
        self.assertEqual(state["forward_win_trials"], {})
        state["bootstrapped"] = True
        state["pending"] = {"context": "initial", "action": "buy_hold",
                            "position": 1}
        BeanAdaptiveGrowthGate._apply_bar(
            state, {"open": 101.0, "close": 102.0}, 2 * BAR, "ETH-USD")
        self.assertEqual(state["forward_win_trials"]["initial"]["buy_hold"], 1)
        self.assertFalse(BeanAdaptiveGrowthGate._apply_bar(
            state, {"open": 101.0, "close": 102.0}, 2 * BAR, "ETH-USD"))
        self.assertEqual(state["forward_win_trials"]["initial"]["buy_hold"], 1)

    def test_v4_historical_q_is_archived_and_requalified_from_zero(self):
        import json
        end = 500 * BAR
        candles = {t: {"open": 100.0, "close": 100.0}
                   for t in range(end - 280 * BAR, end, BAR)}
        with tempfile.TemporaryDirectory() as folder:
            gate = BeanAdaptiveGrowthGate(
                path=Path(folder) / "growth.sqlite3",
                fetcher=lambda _p, a, b: {
                    t: row for t, row in candles.items() if a <= t < b},
                clock=lambda: end + 10)
            old = _fresh_state()
            old["cost_model"] = "taker90bps_held_gap_reward_v4"
            old["q"] = {"initial": {"buy_hold": 99999.0}}
            with gate._db() as db:
                db.execute("INSERT INTO policy(product,state) VALUES(?,?)",
                           ("ETH-USD", json.dumps(old)))
            observed = gate.assess("ETH-USD")
            self.assertEqual(observed["status"], "WARMING_UP")
            self.assertEqual(observed["observed_forward_samples"], 0)
            with gate._db() as db:
                archive = db.execute(
                    "SELECT state FROM policy_archive WHERE product=? "
                    "AND model_version=?",
                    ("ETH-USD", "taker90bps_held_gap_reward_v4")).fetchone()
            self.assertIsNotNone(archive)
            self.assertEqual(
                json.loads(archive["state"])["q"]["initial"]["buy_hold"],
                99999.0)

    def test_two_desktops_replay_new_completed_candle_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        end = 500 * BAR
        bars = {t: {"open": 100.0, "close": 100.1}
                for t in range(end - 280 * BAR, end, BAR)}
        now = [end + 10]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bean.sqlite3"
            def plain_fetch(_product, a, b):
                return {t: row for t, row in bars.items() if a <= t < b}
            initial = BeanAdaptiveGrowthGate(
                path=path, fetcher=plain_fetch, clock=lambda: now[0])
            before = initial.assess("ETH-USD")
            prior = before["reward_updates"]
            bars[end] = {"open": 100.1, "close": 100.2}
            now[0] += BAR
            barrier = Barrier(2)
            def synchronized_fetch(product, a, b):
                market = plain_fetch(product, a, b)
                barrier.wait(timeout=10)
                return market
            readers = [
                BeanAdaptiveGrowthGate(
                    path=path, fetcher=synchronized_fetch,
                    clock=lambda: now[0])
                for _ in range(2)]
            with ThreadPoolExecutor(max_workers=2) as executor:
                outputs = list(executor.map(
                    lambda g: g.assess("ETH-USD"), readers))
            self.assertEqual([x["forward_bars"] for x in outputs], [1, 1])
            self.assertTrue(all(x["status"] != "DATA_UNAVAILABLE" for x in outputs))
            self.assertTrue(all(x["reward_updates"] == prior + 1 for x in outputs))
            final = BeanAdaptiveGrowthGate(
                path=path, fetcher=plain_fetch, clock=lambda: now[0])
            self.assertEqual(final.assess("ETH-USD")["reward_updates"], prior + 1)

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
            self.assertEqual(before["status"], "WARMING_UP")
            prior_updates = before["reward_updates"]
            new_end = end + 100 * BAR
            # Five unavailable bars include the unknowable scheduled fill.
            for t in range(end + 5 * BAR, new_end, BAR):
                bars[t] = {"open": 50.0, "close": 50.0}
            now[0] = new_end + 10
            after = gate.assess("ETH-USD")
            self.assertEqual(after["status"], "WARMING_UP")
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
            self.assertEqual(gate.assess("ETH-USD")["status"], "WARMING_UP")
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

    def test_previous_60bps_reward_model_is_archived_before_90bps_learning(self):
        import json

        end = 500 * BAR
        bars = {ts: {"open": 100.0, "close": 100.0}
                for ts in range(end - 280 * BAR, end, BAR)}
        with tempfile.TemporaryDirectory() as folder:
            gate = BeanAdaptiveGrowthGate(
                path=Path(folder) / "policy.sqlite3",
                fetcher=lambda _p, start, stop: {
                    ts: row for ts, row in bars.items() if start <= ts < stop},
                clock=lambda: end + 10)
            prior = _fresh_state()
            prior["cost_model"] = "taker60bps_held_gap_reward_v3"
            prior["q"] = {"initial": {"buy_hold": 99999.0}}
            with gate._db() as db:
                db.execute("INSERT INTO policy(product,state) VALUES(?,?)",
                           ("ETH-USD", json.dumps(prior)))
            result = gate.assess("ETH-USD")
            self.assertEqual(result["status"], "WARMING_UP")
            self.assertEqual(result["paper_taker_fee_bps"], 90.0)
            with gate._db() as db:
                archived = db.execute(
                    "SELECT state FROM policy_archive WHERE product=? AND model_version=?",
                    ("ETH-USD", "taker60bps_held_gap_reward_v3")).fetchone()
                current = db.execute(
                    "SELECT state FROM policy WHERE product=?",
                    ("ETH-USD",)).fetchone()
            self.assertIsNotNone(archived)
            self.assertEqual(
                json.loads(archived["state"])["q"]["initial"]["buy_hold"], 99999.0)
            updated = json.loads(current["state"])
            self.assertEqual(updated["cost_model"], "taker90bps_forward_context_reward_v5")
            self.assertNotEqual(updated["q"].get("initial", {}).get("buy_hold"), 99999.0)

    def test_mock_round_trip_fee_matches_live_order_cost_assumption(self):
        # The US entry-tier fallback is 90 bps; paid account tiers may differ.
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
            self.assertEqual(result["status"], "WARMING_UP")
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
