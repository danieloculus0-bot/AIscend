"""Native 6h Coinbase market data and full-catalog BEAN ledger regressions."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from src import bean_six_hour_universe as universe
from src.bean_profit_lab import experiment, chain, Ledger
from src.universe_research import shard_for


class SixHourBeanUniverseTests(unittest.TestCase):
    @staticmethod
    def candles(count=240, trend=.0003):
        rows = {}
        price = 100.
        for i in range(count):
            opening = price
            price *= 1+trend
            rows[i*21600] = {"ts":i*21600, "open":opening,
                              "close":price, "volume":10.}
        return rows

    def test_native_granularity_only_two_calls_for_90_days(self):
        queries = []
        clock = lambda: 1730000000
        def fetch(url):
            queries.append(url)
            parsed = urlparse(url)
            fields = parse_qs(parsed.query)
            self.assertEqual(fields["granularity"], ["21600"])
            a = int(datetime.fromisoformat(fields["start"][0]).timestamp())
            b = int(datetime.fromisoformat(fields["end"][0]).timestamp())
            self.assertLessEqual((b-a)//21600, 280)
            return [[ts, 99, 101, 100, 100.5, 5.0]
                    for ts in range(a,b,21600)]
        rows = universe.fetch_history("BTC-USD", days=90,
                                      clock=clock,fetcher=fetch)
        self.assertEqual(len(rows), 360)
        self.assertEqual(len(queries), 2)
        self.assertEqual(next(iter(rows.values()))["open"], 100)
        self.assertEqual(next(iter(rows.values()))["close"], 100.5)

    def test_six_hour_experiment_uses_real_intervals_and_hashes(self):
        trial = experiment("BTC-USD", self.candles(),
                           already_aggregated=True)
        self.assertEqual(trial["period_seconds"], 21600)
        self.assertFalse(trial["promotion_authorized"])
        self.assertEqual(chain(trial["ledger_events"]), trial["ledger_tip"])

    def test_holes_are_not_backfilled(self):
        rows = self.candles()
        rows.pop(110*21600)
        trial = experiment("BTC-USD", rows, already_aggregated=True)
        self.assertFalse(trial["promotion_authorized"])
        self.assertIn(trial["status"], ("DATA_GAP", "NOT_PROVEN"))

    def test_full_aggregation_requires_identical_catalog_and_all_shards(self):
        products = ["AAA-USD", "BBB-USD", "CCC-USD", "DDD-USD"]
        catalog = {"eligible_quote_markets": products}
        def market_row(product):
            ledger = Ledger()
            ledger.append("market_terminal", product=product,
                          status="NOT_PROVEN", promotion_authorized=False)
            return {"status": "NOT_PROVEN", "promotion_authorized": False,
                    "ledger_tip": ledger.tip}
        reports = []
        for shard in range(2):
            reports.append({"shards":2,"shard_index":shard,
                "catalog_hash":"frozen-hash","catalog":catalog,
                "days":90,"fee_bps":60,"spread_bps":10,
                "products":{p:market_row(p)
                            for p in products if shard_for(p,2)==shard}})
        combined = universe.aggregate(reports)
        self.assertEqual(combined["market_count"], 4)
        self.assertFalse(combined["promotion_authorized"])
        with self.assertRaises(ValueError):
            universe.aggregate(reports[:1])
        with self.assertRaises(ValueError):
            universe.aggregate([reports[0],dict(reports[1],catalog_hash="changed")])

    def test_every_market_attempt_has_a_hash_receipt(self):
        products = ["AAA-USD", "BBB-USD", "CCC-USD", "DDD-USD"]
        good = self.candles()
        with_gap = dict(good)
        with_gap.pop(110 * universe.BAR)
        histories = {products[0]: good, products[1]: with_gap,
                     products[2]: self.candles(count=30)}
        def history(product, *, days):
            if product not in histories:
                raise TimeoutError("missing market history")
            return histories[product]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(universe, "discover_public_spot",
                              return_value={"eligible_quote_markets": products}):
                report = universe.run_shard(shards=1, shard_index=0, workers=2,
                    root=Path(folder), history_fetcher=history)
            self.assertEqual(report["attempted"], 4)
            self.assertEqual(report["products"][products[1]]["status"], "DATA_GAP")
            self.assertEqual(report["products"][products[2]]["status"], "INSUFFICIENT_HISTORY")
            self.assertEqual(report["products"][products[3]]["status"], "DATA_UNAVAILABLE")
            for product, result in report["products"].items():
                receipt = Path(folder) / (product.replace("-", "_") + ".jsonl")
                events = [json.loads(line) for line in receipt.read_text().splitlines()]
                self.assertEqual(chain(events), result["ledger_tip"])
                self.assertEqual(events[-1]["type"], "market_terminal")
                self.assertEqual(events[-1]["product"], product)
                self.assertEqual(events[-1]["status"], result["status"])
                self.assertFalse(events[-1]["promotion_authorized"])
            self.assertEqual(universe.aggregate([report])["market_count"], 4)
            with self.assertRaises(ValueError):
                broken = dict(report, products={
                    **report["products"], products[3]: {
                        "status": "DATA_UNAVAILABLE", "promotion_authorized": False}})
                universe.aggregate([broken])
            with patch.object(universe, "discover_public_spot",
                              return_value={"eligible_quote_markets": products}):
                with self.assertRaises(FileExistsError):
                    universe.run_shard(shards=1, shard_index=0, workers=2,
                        root=Path(folder), history_fetcher=history)

    def test_full_universe_fails_closed_on_empty_or_bad_candles(self):
        with self.assertRaises(ValueError):
            universe.fetch_history("BTC-USD",days=90,fetcher=lambda url:{},
                                   clock=lambda:1730000000)


if __name__ == "__main__":
    unittest.main()
