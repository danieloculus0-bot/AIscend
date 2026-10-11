import unittest
from src.launch_analysis import evaluate_launches


class CoinbaseLaunchTests(unittest.TestCase):
    def test_new_coin_can_win_after_cost(self):
        result = evaluate_launches(
            [{"product": "NEW-USDC", "ts": 1000, "stage": "FULL_TRADING"}],
            {"NEW-USDC": [(995, 0.1), (1000, 1), (1300, 1.2)]},
            horizons=(300,))
        self.assertAlmostEqual(result["by_horizon"]["300"]["median_net_return"],
                               0.188)
        self.assertEqual(result["by_horizon"]["300"]["win_rate"], 1)

    def test_launches_do_not_always_pump(self):
        result = evaluate_launches(
            [{"product": "RUG-USDC", "ts": 1000, "stage": "FULL_TRADING"}],
            {"RUG-USDC": [(1000, 1), (1300, 0.75)]},
            horizons=(300,))
        self.assertEqual(result["by_horizon"]["300"]["win_rate"], 0)

    def test_missing_observation_is_not_a_zero_return(self):
        result = evaluate_launches(
            [{"product": "X-USDC", "ts": 1000, "stage": "FULL_TRADING"}],
            {"X-USDC": [(1000, 1), (1200, 2)]}, horizons=(300,))
        self.assertIsNone(result["by_horizon"]["300"]["median_net_return"])
        self.assertEqual(result["by_horizon"]["300"]["count"], 0)

    def test_pre_listing_quote_is_not_entry(self):
        report = evaluate_launches(
            [{"product": "X-USDC", "ts": 1000, "stage": "FULL_TRADING"}],
            {"X-USDC": [(900, 0.1), (1000, 1), (1300, 1.05)]}, horizons=(300,))
        self.assertAlmostEqual(report["by_horizon"]["300"]["median_net_return"],
                               0.038)


if __name__ == "__main__":
    unittest.main()
