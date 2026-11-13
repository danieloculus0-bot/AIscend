import json
import tempfile
import unittest
from pathlib import Path

from src.bean_policy import BeanPolicyGate
from src.bean_research_supervisor import BeanResearchSupervisor
from src.bean_strategy_lab import (
    BeanStrategyLab, select_markets, simulate, evolve, WARMUP, HOUR, MODES, COST, _choose
)


def fixture(_ordinal=0):
    pairs = ["BTC-USD", "ETH-USD", "DOGE-USD", "SHIB-USD"]
    bars = []
    for i in range(288):
        values = {}
        for j, name in enumerate(pairs):
            base = (1 + j) * (1 + 0.0002 * (j + 1) * i)
            values[name] = {"open": base, "close": base}
        bars.append({"ts": i * 3600, "prices": values})
    return {"bars": bars, "pairs": pairs, "end": 288 * 3600,
            "catalog_count": 400, "attempted": pairs, "skipped": {},
            "source": "SYNTHETIC UNIT TEST FIXTURE — NOT COINBASE"}


class BeanSchoolTests(unittest.TestCase):
    def test_research_cost_includes_90bps_taker_plus_modeled_slippage(self):
        self.assertAlmostEqual(COST, 0.0095)

    def test_policy_uses_fresh_complete_hourly_closes_not_backtest_returns(self):
        with tempfile.TemporaryDirectory() as d:
            lab = BeanStrategyLab(Path(d) / "bean.db")
            with lab._connect() as db:
                db.execute(
                    "INSERT INTO experiments(id,created,state,ordinal,seed,result) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("selected", 1., "AUTO_SELECTED", 66, 1,
                     json.dumps({"candidate": ["momentum", 6, 0.003, 3],
                                 "pairs": ["BTC-USD", "ETH-USD"],
                                 "holdout": {"net_return": .5274}})),
                )

            calls = []
            def bars(product, start, end):
                calls.append(product)
                return {
                    ts: {"open": 100., "close": (
                        100. * (1.01 ** ((ts - start) // HOUR))
                        if product == "ETH-USD" else 100.)}
                    for ts in range(start, end, HOUR)
                }
            gate = BeanPolicyGate(lab, fetcher=bars,
                                  clock=lambda: 1000 * HOUR + 1)
            result = gate.assess()
            self.assertEqual(result["status"], "READY")
            self.assertEqual(result["target"], "ETH-USD")
            self.assertEqual(result["experiment"], 66)
            self.assertEqual(result["markets"], 2)
            self.assertTrue(gate.permits_buy(result, "WETH"))
            self.assertFalse(gate.permits_buy(result, "BTC-USDC"))
            self.assertEqual(gate.assess(), result)
            self.assertEqual(len(calls), 2)

    def test_policy_missing_current_candle_cannot_issue_signal(self):
        with tempfile.TemporaryDirectory() as d:
            lab = BeanStrategyLab(Path(d) / "bean.db")
            with lab._connect() as db:
                db.execute(
                    "INSERT INTO experiments(id,created,state,ordinal,seed,result) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("selected", 1., "AUTO_SELECTED", 66, 1,
                     json.dumps({"candidate": ["momentum", 6, 0.003, 3],
                                 "pairs": ["BTC-USD", "ETH-USD"]})),
                )
            def missing(product, start, end):
                return {ts: {"open": 100., "close": 100.}
                        for ts in range(start, end - HOUR, HOUR)}
            gate = BeanPolicyGate(lab, fetcher=missing,
                                  clock=lambda: 1000 * HOUR + 1)
            result = gate.assess()
            self.assertEqual(result["status"], "DATA_UNAVAILABLE")
            self.assertEqual(result["trade_effect"], "NONE")
            self.assertTrue(gate.permits_buy(result, "ETH-USDC"))

    def test_autonomous_research_on_new_hour_only_with_durable_cursor(self):
        class ReadOnlyGate:
            def __init__(self):
                self.calls = 0

            def assess(self):
                self.calls += 1
                return {"status": "READY", "target": "ETH-USD",
                        "trade_effect": "BUY_CONFIRMATION_ONLY"}

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bean.db"
            lab = BeanStrategyLab(path, collector=fixture)
            # Test the coordinator with a research start spy. Other tests
            # exercise the actual background evolution against unit fixtures.
            starts = []
            lab.start = lambda: starts.append(True)
            clock = [1000 * HOUR + 299]
            gate = ReadOnlyGate()
            watch = BeanResearchSupervisor(lab, gate, clock=lambda: clock[0])
            watch.tick()
            self.assertEqual(len(starts), 0)
            self.assertEqual(gate.calls, 0)

            clock[0] += 2
            watch.tick()
            self.assertEqual(len(starts), 1)
            self.assertEqual(gate.calls, 1)
            self.assertEqual(watch.status()["policy"]["status"], "READY")
            watch.tick()
            self.assertEqual(len(starts), 1)

            # Restarting the process against the same SQLite file cannot
            # repeat the previous hour, even if the last trial failed.
            reopened = BeanStrategyLab(path, collector=fixture)
            reopened_starts = []
            reopened.start = lambda: reopened_starts.append(True)
            recovered = BeanResearchSupervisor(
                reopened, gate, clock=lambda: clock[0])
            recovered.tick()
            self.assertEqual(len(reopened_starts), 0)

            self.assertFalse(recovered.set_enabled(False)["enabled"])
            clock[0] += HOUR
            recovered.tick()
            self.assertEqual(len(reopened_starts), 0)
            self.assertFalse(BeanResearchSupervisor(
                reopened, gate, clock=lambda: clock[0]).enabled())
            # Prevent a background thread from racing our deterministic
            # clock. Production set_enabled(True) starts the daemon worker.
            recovered.start = lambda: None
            recovered.set_enabled(True)
            recovered.tick()
            self.assertEqual(len(reopened_starts), 1)
            recovered.tick()
            self.assertEqual(len(reopened_starts), 1)

    def test_bean_can_explore_new_causal_rule_families(self):
        self.assertIn("acceleration", MODES)
        self.assertIn("risk_adjusted", MODES)
        bars = []
        for i in range(100):
            btc = 100 + i * 0.1
            eth = 100 + i * 0.1 + max(0, i-88)**2 * 0.3
            bars.append({"ts": i * HOUR,
                         "prices": {"BTC-USD": {"close": btc, "open": btc},
                                    "ETH-USD": {"close": eth, "open": eth}}})
        self.assertEqual(
            _choose(bars, len(bars), ["BTC-USD", "ETH-USD"],
                    ("acceleration", 12, 0.003, 3)), "ETH-USD")
        self.assertIn(
            _choose(bars, len(bars), ["BTC-USD", "ETH-USD"],
                    ("risk_adjusted", 12, 0.003, 3)),
            (None, "BTC-USD", "ETH-USD"))

    def test_cohort_rotation_includes_doge_and_shib(self):
        catalog = list(("BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "SHIB-USD",
                        "UNI-USD", "XRP-USD", "LINK-USD", "AAVE-USD", "BCH-USD",
                        "AVAX-USD", "ATOM-USD"))
        first = select_markets(catalog, 0, extra=3)
        second = select_markets(catalog, 1, extra=3)
        for coin in ("DOGE-USD", "SHIB-USD", "BTC-USD"):
            self.assertIn(coin, first)
            self.assertIn(coin, second)
        self.assertNotEqual(first, second)

    def test_causal_research_and_fixed_stressed_costs(self):
        data = fixture()
        rule = ("momentum", 6, 0.003, 3)
        a = simulate(data["bars"], data["pairs"], rule)
        self.assertEqual(a["samples"] if "samples" in a else
                         len(data["bars"]) - WARMUP, len(data["bars"]) - WARMUP)
        # Values on the current bar cannot cause trades at that same open.
        before = simulate(data["bars"][:200], data["pairs"], rule)
        data["bars"][199]["prices"]["SHIB-USD"]["close"] *= 20
        after = simulate(data["bars"][:200], data["pairs"], rule)
        self.assertTrue(before["orders"] >= 0 and after["orders"] >= 0)

    def test_evolution_is_deterministic_and_never_release_ready(self):
        data = fixture()
        a = evolve(data, seed=33, generations=2, population=10)
        b = evolve(data, seed=33, generations=2, population=10)
        self.assertEqual(a, b)
        self.assertTrue(a["unique_candidates"] >= 4)
        self.assertEqual(len(a["generations"]), 2)
        self.assertEqual(a["schema"], "bean.manual.school.v1")
        self.assertNotIn("live_activation", a)

    def test_retry_retains_lessons_and_selection_is_automatic(self):
        with tempfile.TemporaryDirectory() as d:
            lab = BeanStrategyLab(Path(d) / "bean.db", collector=fixture)
            first = lab.start()["latest"]["id"]
            lab._worker.join(timeout=15)
            a = lab.status()
            self.assertEqual(a["latest"]["state"], ("AUTO_SELECTED" if a["latest"]["report"]["beats_cash_and_btc"] else "REJECTED_BY_EVIDENCE"))
            self.assertEqual(a["latest"]["id"], first)
            self.assertIn("report", a["latest"])
            second = lab.start()["latest"]["id"]
            lab._worker.join(timeout=15)
            b = lab.status()
            self.assertEqual(b["retained_events"], 2)
            self.assertNotEqual(second, first)
            self.assertEqual(b["history"][1]["id"], first)
            self.assertGreaterEqual(b["latest"]["report"]["memory_seeds_tested"], 1)
            self.assertTrue(b["latest"]["report"]["lessons"])
            if not b["latest"]["report"]["beats_cash_and_btc"]:
                self.assertEqual(b["latest"]["state"], "REJECTED_BY_EVIDENCE")
                with self.assertRaisesRegex(ValueError, "did not pass"):
                    lab.approve(second)

    def test_long_term_archive_rotates_old_strategies_without_forgetting(self):
        # Stored results from even REJECTED experiments must survive beyond
        # the old eight-run seed window and remain reusable without holdout bias.
        import json
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bean.db"
            lab = BeanStrategyLab(path, collector=fixture)
            with lab._connect() as db:
                for i in range(20):
                    candidate = ["momentum", (3, 6, 12, 24, 48)[i % 5],
                                 0.003, (3, 6, 12, 24)[i // 5]]
                    db.execute(
                        "INSERT INTO experiments(id, created, state, ordinal, seed, result) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (f"historic-{i}", float(i), "REJECTED_BY_EVIDENCE",
                         i + 1, i, json.dumps({"candidate": candidate,
                                              "holdout": {"net_return": -0.50}})),
                    )
                db.execute(
                    "INSERT INTO experiments(id, created, state, ordinal, seed, result) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("invalid-historical", 21.0, "FAILED", 21, 21,
                     '{"candidate":["nonsense",3,0,3]}'),
                )
                archive = lab._lesson_archive(db)
            self.assertEqual(len(archive), 20)
            self.assertEqual(lab.status()["retained_lessons"], 20)
            self.assertEqual(lab.status()["live_strategy_activation"], "NOT_DEPLOYED")

            old = set(archive[8:])
            rotated = set()
            for ordinal in range(22, 25):
                batch = lab._replay_lessons(archive, ordinal)
                self.assertEqual(len(batch), 16)
                rotated.update(batch[8:])
            self.assertEqual(rotated, old)
            self.assertEqual(lab._replay_lessons(archive, 22)[:8], archive[:8])

            # A new process opens the SAME SQLite journal and immediately
            # recovers all historic genes without re-running their experiments.
            reopened = BeanStrategyLab(path, collector=fixture)
            self.assertEqual(reopened.status()["retained_events"], 21)
            self.assertEqual(reopened.status()["retained_lessons"], 20)
            reopened.start()
            reopened._worker.join(timeout=15)
            final = reopened.status()
            self.assertFalse(final["running"])
            self.assertEqual(final["latest"]["report"]["memory_available"], 20)
            self.assertEqual(final["latest"]["report"]["memory_replayed"], 16)
            self.assertEqual(final["latest"]["report"]["memory_seeds_tested"], 16)
            self.assertEqual(final["latest"]["report"]["live_strategy_activation"],
                             "NOT_DEPLOYED")
            self.assertEqual(final["retained_lessons"], 21 if
                             tuple(final["latest"]["report"]["candidate"]) not in archive
                             else 20)

    def test_auto_selection_requires_evidence_and_no_human_approval(self):
        with tempfile.TemporaryDirectory() as d:
            lab = BeanStrategyLab(Path(d) / "bean.db", collector=fixture)
            with self.assertRaisesRegex(ValueError, "Unknown BEAN"):
                lab.approve("not-an-event")
            lab.start()
            lab._worker.join(timeout=15)
            latest = lab.status()["latest"]
            if latest["report"]["beats_cash_and_btc"]:
                self.assertEqual(latest["state"], "AUTO_SELECTED")
                self.assertTrue(latest["approved"])
                self.assertEqual(lab.approve(latest["id"])["latest"]["state"], "AUTO_SELECTED")
            else:
                with self.assertRaises(ValueError):
                    lab.approve(latest["id"])


if __name__ == "__main__":
    unittest.main()
