import unittest

from scripts.bean_evolution_lab import (
    WARMUP, evolve, normalize, signals, simulate, mutate, random_genome,
)
import random


def fixture(length=600, trend=0.0):
    pairs = ("BTC-USD", "ETH-USD", "DOGE-USD", "SHIB-USD")
    bars = []
    for i in range(length):
        prices = {}
        for n, pair in enumerate(pairs):
            p = 1.0 + n + i * trend * (1.0 + n / 4.0)
            prices[pair] = {"open": p, "close": p}
        bars.append({"ts": i * 3600, "prices": prices})
    return {"source": "SYNTHETIC TEST DATA, NOT COINBASE", "pairs": list(pairs),
            "bars": bars}


class EvolutionTests(unittest.TestCase):
    def test_reproducible_brute_force_generations(self):
        data = fixture(trend=0.00001)
        a = evolve(data, generations=2, population=8, seed=17)
        b = evolve(data, generations=2, population=8, seed=17)
        self.assertEqual(a, b)
        self.assertEqual(len(a["survivors"]), 2)
        self.assertGreaterEqual(a["candidates_tested"], 8)
        self.assertFalse(a["release_ready"])
        self.assertFalse(a["model_authored"])
        self.assertIn("DOGE-USD", a["pairs"])

    def test_cash_is_a_valid_outcome_and_costs_are_real(self):
        bars = fixture(500)["bars"]
        g = normalize({"mode": "momentum", "fast": 6, "slow": 48,
                       "threshold": 0.035, "rebalance": 12,
                       "max_positions": 1, "allocation": 1.0,
                       "stop_loss": 0.04, "take_profit": 0.16})
        result = simulate(bars, g, list(bars[0]["prices"]))
        self.assertEqual(result["trades"], 0)
        self.assertEqual(result["final"], 25.0)
        self.assertEqual(result["samples"], len(bars) - WARMUP)

    def test_current_hour_cannot_change_signal(self):
        data = fixture(500, trend=0.0001)
        bars = data["bars"]
        g = normalize({"mode": "dual_trend", "fast": 6, "slow": 48,
                       "threshold": 0.0, "rebalance": 12,
                       "max_positions": 1, "allocation": 1.0,
                       "stop_loss": 0.04, "take_profit": 0.16})
        before = signals(bars, 250, data["pairs"], g)
        bars[250]["prices"]["DOGE-USD"]["close"] *= 100000
        self.assertEqual(before, signals(bars, 250, data["pairs"], g))

    def test_mutation_changes_parameters(self):
        rng = random.Random(6410)
        parent = random_genome(rng)
        child = mutate(parent, rng)
        self.assertEqual(set(parent), set(child))
        self.assertGreaterEqual(child["slow"], child["fast"] + 1)

    def test_missing_or_noncontiguous_data_rejected(self):
        bad = fixture()
        bad["bars"][200]["ts"] += 3
        with self.assertRaisesRegex(ValueError, "Non-contiguous"):
            evolve(bad, generations=1, population=8)
        bad = fixture()
        bad["bars"][200]["prices"]["DOGE-USD"]["close"] = float("nan")
        with self.assertRaisesRegex(ValueError, "Missing or invalid"):
            evolve(bad, generations=1, population=8)


if __name__ == "__main__":
    unittest.main()
