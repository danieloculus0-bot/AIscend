import unittest
from scripts.bean_historical_replay import run, assess, discover_products, fetch_candles, START
from io import BytesIO


def fixture():
    bars = []
    for t in range(180):
        prices = {}
        for i, p in enumerate(("BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD")):
            value = 100 * (1 + 0.0005 * (i + 1)) ** t
            prices[p] = {"open": value, "close": value * 1.0001}
        bars.append({"ts": t * 3600, "prices": prices})
    return {"source": "test-fixture-not-real", "pairs": list(bars[0]["prices"]), "bars": bars}


class HistoricalReplayTests(unittest.TestCase):
    def test_replay_deterministic(self):
        d = fixture()
        first, second = assess(d), assess(d)
        first.pop("as_of")
        second.pop("as_of")
        self.assertEqual(first, second)

    def test_no_forced_level_one(self):
        result = assess(fixture())
        self.assertFalse(result["level_one_achieved"])
        self.assertLess(result["holdout"]["final"], 100000)

    def test_transaction_costs_are_applied(self):
        bars = fixture()["bars"]
        hold = run(bars, "buy_hold", 3, 0)
        cash = run(bars, "cash", 3, 0)
        self.assertEqual(cash["final"], START)
        self.assertGreater(hold["trades"], 0)

    def test_missing_hour_rejected(self):
        data = fixture()
        data["bars"][77]["ts"] += 1
        with self.assertRaisesRegex(ValueError, "Non-contiguous"):
            assess(data)

    def test_cash_can_win_when_all_strategies_are_bad(self):
        data = fixture()
        for bar in data["bars"]:
            for item in bar["prices"].values():
                item["open"] = 100.0
                item["close"] = 100.0
        report = assess(data)
        self.assertEqual(report["strategy"]["rule"], "cash")
        self.assertAlmostEqual(report["holdout"]["final"], START)

    def test_new_strategy_families_preserve_accounting(self):
        data = fixture()
        for family in ("trend", "persistent"):
            result = run(data["bars"], family, 12, 0.005)
            self.assertGreater(result["final"], 0)
            self.assertGreaterEqual(result["trades"], 0)
            self.assertLessEqual(result["max_drawdown"], 1)

    def test_dynamic_pair_universe(self):
        data = fixture()
        for bar in data["bars"]:
            bar["prices"]["DOGE-USD"] = {"open": 0.12, "close": 0.12}
            bar["prices"]["SHIB-USD"] = {"open": 0.00001, "close": 0.00001}
        data["pairs"].extend(("DOGE-USD", "SHIB-USD"))
        result = assess(data)
        self.assertEqual(result["pairs"], data["pairs"])
        self.assertIn("DOGE-USD", result["pairs"])
        self.assertIn("SHIB-USD", result["pairs"])

    def test_coinbase_catalog_includes_meme_pairs_and_prefers_usd(self):
        import json
        payload = [
            {"id":"DOGE-USD", "base_currency":"DOGE", "quote_currency":"USD", "status":"online"},
            {"id":"SHIB-USDC", "base_currency":"SHIB", "quote_currency":"USDC", "status":"online"},
            {"id":"SHIB-USD", "base_currency":"SHIB", "quote_currency":"USD", "status":"online"},
            {"id":"BAD-USD", "base_currency":"BAD", "quote_currency":"USD", "status":"offline"},
        ]
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        result = discover_products(request_fn=lambda request,timeout: Response(json.dumps(payload).encode()))
        self.assertEqual(result, ["DOGE-USD", "SHIB-USD"])

    def test_rate_limit_is_retried_without_substituting_fake_candles(self):
        import json
        from unittest.mock import patch
        from urllib.error import HTTPError
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        attempts = [0]
        def request(req, timeout):
            attempts[0] += 1
            if attempts[0] == 1:
                raise HTTPError(req.full_url, 429, "Too Many Requests",
                                {"Retry-After": "1"}, None)
            return Response(json.dumps({"candles": [
                {"start": "0", "open": "1.0", "close": "1.1"}]}).encode())
        with patch("scripts.bean_historical_replay.time.sleep") as sleep:
            bars = fetch_candles("DOGE-USD", 0, 3600, request_fn=request)
        self.assertEqual(attempts[0], 2)
        self.assertEqual(bars[0]["close"], 1.1)
        sleep.assert_called_once()

    def test_missing_data_rejected(self):
        with self.assertRaises(ValueError):
            assess({"bars": [], "source": "missing"})


if __name__ == "__main__":
    unittest.main()
