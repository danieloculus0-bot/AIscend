import unittest
from scripts.bean_optimization_lab import (
    BASELINE, FEE, REGIMES, candidates, evaluate, experiment, market, simulate
)


class BeanOptimizationTests(unittest.TestCase):
    def test_deterministic_markets(self):
        self.assertEqual(market(22, "bull"), market(22, "bull"))
        self.assertNotEqual(market(22, "bull"), market(23, "bull"))
        self.assertEqual(len(REGIMES), 4)

    def test_no_lookahead_and_no_free_trades(self):
        stationary = [1.0] * 20
        result = simulate(stationary, {"entry": -0.001, "exit": -0.5,
                                      "allocation": 0.5})
        self.assertLess(result["net"], 1.0)
        self.assertGreater(result["trades"], 0)
        self.assertGreater(FEE, 0)

    def test_replay_identical(self):
        self.assertEqual(experiment(), experiment())

    def test_holdout_is_separate_and_nonexecuting(self):
        report = experiment()
        self.assertTrue(report["research_only"])
        self.assertEqual(report["market"], "synthetic_only")
        self.assertNotEqual(report["train_seed_start"], report["holdout_seed_start"])
        self.assertEqual(report["candidate_count"], len(list(candidates())))
        self.assertEqual(report["baseline_policy"], BASELINE)
        self.assertGreater(report["baseline_holdout"]["runs"], 0)
        self.assertEqual(report["baseline_holdout"]["runs"],
                         report["challenger_holdout"]["runs"])

    def test_score_finite_across_regimes(self):
        for item in candidates():
            scores = evaluate(item, range(2))
            self.assertGreater(scores["mean_net"], 0)
            self.assertTrue(-100 < scores["score"] < 100)
            self.assertTrue(0 <= scores["loss_frequency"] <= 1)


if __name__ == "__main__":
    unittest.main()
