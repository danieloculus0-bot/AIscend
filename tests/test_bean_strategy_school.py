import unittest
from scripts.bean_strategy_school import learn


class SchoolTests(unittest.TestCase):
    def test_school_records_iterations_without_claiming_model_authorship(self):
        prices = {}
        bars = []
        for hour in range(180):
            prices = {p: {"open": 100 + hour * (i+1)*.01,
                          "close": 100 + hour * (i+1)*.01}
                      for i,p in enumerate(("BTC-USD","ETH-USD","SOL-USD","ADA-USD"))}
            bars.append({"ts":hour*3600,"prices":prices})
        result = learn({"source":"test fixture","bars":bars})
        self.assertEqual(len(result["history"]), 4)
        self.assertFalse(result["model_authored"])
        self.assertEqual(sum(item["candidates"] for item in result["history"]),80)
        self.assertIn("profitable_holdout", result)
        self.assertEqual(result, learn({"source":"test fixture","bars":bars}))


if __name__ == "__main__":
    unittest.main()
