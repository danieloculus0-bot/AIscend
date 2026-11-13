import unittest
from scripts.bean_universe_challenge import select_cohort, collect_cohort


class CohortTests(unittest.TestCase):
    def test_every_nonbaseline_market_eventually_gets_a_shard(self):
        products = ("BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD",
                    "DOGE-USD", "SHIB-USD", "XRP-USD", "LINK-USD", "UNI-USD")
        seen = set()
        count = None
        for i in range(10):
            chosen, shards = select_cohort(products, shard=i, per_run=2)
            seen.update(chosen)
            count = shards
        self.assertEqual(count, 3)
        self.assertEqual(seen, set(products))

    def test_missing_new_listing_history_is_reported_not_fabricated(self):
        products = ("BTC-USD", "ETH-USD", "DOGE-USD", "SHIB-USD")
        hours = 480
        def candles(pair, start, end):
            values = {t: {"open": 1.0, "close": 1.0}
                      for t in range(start, end, 3600)}
            if pair == "SHIB-USD":
                del values[start]
            return values
        dataset, manifest = collect_cohort(
            0, hours * 3600, products, shard=0, per_run=3,
            candle_fn=candles
        )
        self.assertIsNotNone(dataset)
        self.assertEqual(len(dataset["bars"]), hours)
        self.assertIn("DOGE-USD", dataset["pairs"])
        self.assertNotIn("SHIB-USD", dataset["pairs"])
        self.assertIn("SHIB-USD", manifest["skipped"])

    def test_benchmark_missing_returns_no_claim(self):
        def fail(pair, start, end):
            raise RuntimeError("No history")
        dataset, manifest = collect_cohort(
            0, 500 * 3600, ("DOGE-USD",), per_run=2, candle_fn=fail)
        self.assertIsNone(dataset)
        self.assertIn("DOGE-USD", manifest["skipped"])


if __name__ == "__main__":
    unittest.main()
