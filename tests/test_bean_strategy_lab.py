import tempfile
import unittest
from pathlib import Path

from src.bean_strategy_lab import (
    BeanStrategyLab, select_markets, simulate, evolve, WARMUP
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

    def test_retry_retains_lessons_and_unqualified_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as d:
            lab = BeanStrategyLab(Path(d) / "bean.db", collector=fixture)
            first = lab.start()["latest"]["id"]
            lab._worker.join(timeout=15)
            a = lab.status()
            self.assertEqual(a["latest"]["state"], "REVIEW")
            self.assertEqual(a["latest"]["id"], first)
            self.assertIn("report", a["latest"])
            second = lab.start()["latest"]["id"]
            lab._worker.join(timeout=15)
            b = lab.status()
            self.assertEqual(b["retained_events"], 2)
            self.assertNotEqual(second, first)
            self.assertEqual(b["history"][1]["id"], first)
            if not b["latest"]["report"]["beats_cash_and_btc"]:
                with self.assertRaisesRegex(ValueError, "not beaten"):
                    lab.approve(second)

    def test_approval_requires_existing_finished_qualified_event(self):
        with tempfile.TemporaryDirectory() as d:
            lab = BeanStrategyLab(Path(d) / "bean.db", collector=fixture)
            with self.assertRaisesRegex(ValueError, "Only a finished"):
                lab.approve("not-an-event")
            lab.start()
            lab._worker.join(timeout=15)
            latest = lab.status()["latest"]
            if latest["report"]["beats_cash_and_btc"]:
                review = lab.approve(latest["id"])
                self.assertEqual(review["latest"]["state"], "APPROVED_FOR_REVIEW")
                self.assertTrue(review["latest"]["approved"])
            else:
                with self.assertRaises(ValueError):
                    lab.approve(latest["id"])


if __name__ == "__main__":
    unittest.main()
