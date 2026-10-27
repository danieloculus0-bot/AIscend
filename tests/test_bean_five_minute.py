import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from src.bean_five_minute import BAR, BARS, FastPaperBook, collect_5m


def candles(product, start, end):
    result = {}
    for ts in range(start, end, BAR):
        step = (ts-start)//BAR
        price = (100 + step*0.4 if product == "ETH-USD"
                 else 100 + step*0.05)
        result[ts] = {"open": price, "close": price}
    return result


class FiveMinuteTests(unittest.TestCase):
    def test_real_five_minute_intervals_without_price_interpolation(self):
        cohort = ["BTC-USD", "ETH-USD", "SOL-USD"]
        end = 1000*BAR
        got = collect_5m(0, catalog=cohort, fetcher=candles, end=end)
        self.assertEqual(got["granularity_seconds"], BAR)
        self.assertEqual(len(got["bars"]), BARS)
        self.assertEqual(got["bars"][-1]["ts"], end-BAR)
        self.assertEqual(got["pairs"], cohort)
        def missing(product, start, finish):
            row = candles(product, start, finish)
            if product == "BTC-USD":
                row.pop(finish-BAR)
            return row
        with self.assertRaisesRegex(ValueError, "contiguous fresh"):
            collect_5m(0, catalog=cohort, fetcher=missing, end=end)

    def test_forward_account_only_fills_after_next_complete_candle(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"paper.db"
            now = [1000*BAR+60]
            book = FastPaperBook(path=path, fetcher=candles, clock=lambda: now[0])
            selected = {"id": "pass-1", "ordinal": 9,
                        "state": "AUTO_SELECTED",
                        "report": {"candidate": ["momentum",6,0.003,3],
                                   "pairs": ["BTC-USD", "ETH-USD"]}}
            self.assertTrue(book.enqueue(selected))
            self.assertFalse(book.enqueue(selected))
            first = book.step(end=1000*BAR)
            self.assertEqual(first["status"], "WAITING")
            self.assertEqual(first["orders"], 0)
            self.assertEqual(first["cash"], 25.0)
            self.assertEqual(first["samples"], 0)
            self.assertEqual(book.step(end=1000*BAR)["status"], "WAITING")
            now[0] += BAR
            second = book.step(end=1001*BAR)
            self.assertEqual(second["status"], "WAITING")
            self.assertEqual(second["samples"], 0)
            now[0] += BAR
            third = book.step(end=1002*BAR)
            self.assertEqual(third["status"], "ACTIVE")
            self.assertEqual(third["samples"], 1)
            self.assertEqual(third["orders"], 0)
            self.assertIsNone(third["held"])
            now[0] += BAR
            fourth = book.step(end=1003*BAR)
            self.assertEqual(fourth["status"], "ACTIVE")
            # The first pending signal was recorded after this candle opened.
            self.assertEqual(fourth["orders"], 0)
            self.assertIsNone(fourth["held"])
            now[0] += BAR
            fifth = book.step(end=1004*BAR)
            self.assertEqual(fifth["status"], "ACTIVE")
            self.assertEqual(fifth["orders"], 1)
            self.assertEqual(fifth["held"], "ETH-USD")
            self.assertGreaterEqual(fifth["samples"], 3)
            self.assertEqual(third["source"],
                             "Forward paper, next-bar open fills, modeled costs")
            self.assertEqual(FastPaperBook(path=path, fetcher=candles).status()["orders"], 1)

    def test_future_or_unaligned_end_cannot_fabricate_forward_samples(self):
        with tempfile.TemporaryDirectory() as td:
            now = [1000*BAR+60]
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=candles, clock=lambda: now[0])
            self.assertTrue(book.enqueue({
                "id":"future-guard","ordinal":1,"state":"AUTO_SELECTED",
                "report":{"candidate":["momentum",6,0.003,3],
                          "pairs":["BTC-USD","ETH-USD"]}}))
            with self.assertRaisesRegex(ValueError, "current completed boundary"):
                book.step(end=1001*BAR)
            with self.assertRaisesRegex(ValueError, "unaligned"):
                book.step(end=1000*BAR+1)
            self.assertEqual(book.status()["samples"],0)
            self.assertEqual(book.status()["orders"],0)
            self.assertEqual(book.step(end=1000*BAR)["samples"],0)
            now[0] += BAR
            self.assertEqual(book.step(end=1001*BAR)["samples"],0)
            now[0] += BAR
            self.assertEqual(book.step(end=1002*BAR)["samples"],1)

    def test_legacy_pending_without_observation_time_cannot_backfill(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"paper.db"
            now=[1000*BAR+60]
            book=FastPaperBook(path=path,fetcher=candles,clock=lambda:now[0])
            book.enqueue({"id":"legacy","ordinal":1,"state":"AUTO_SELECTED",
                          "report":{"candidate":["momentum",6,0.003,3],
                                    "pairs":["BTC-USD","ETH-USD"]}})
            self.assertEqual(book.step(end=1000*BAR)["status"],"WAITING")
            now[0] += BAR
            self.assertEqual(book.step(end=1001*BAR)["status"],"WAITING")
            now[0] += BAR
            first=book.step(end=1002*BAR)
            self.assertIsNotNone(first["pending"])
            with closing(sqlite3.connect(path)) as db:
                raw=db.execute("SELECT data FROM paper WHERE id='legacy'").fetchone()[0]
                legacy=json.loads(raw)
                legacy.pop("pending_recorded_at",None)
                db.execute("UPDATE paper SET data=? WHERE id='legacy'",
                           (json.dumps(legacy),))
                db.commit()
            now[0]+=BAR
            second=book.step(end=1003*BAR)
            self.assertEqual(second["orders"],0)
            self.assertIsNone(second["held"])
            self.assertGreater(second["pending_recorded_at"],1002*BAR)

    def test_missing_market_bar_is_not_synthetic_profit(self):
        with tempfile.TemporaryDirectory() as td:
            now = [1000*BAR+60]
            def missing(product, start, end):
                data = candles(product, start, end)
                if product == "ETH-USD":
                    data.pop(end-BAR)
                return data
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=missing, clock=lambda: now[0])
            book.enqueue({"id":"candidate","ordinal":1,"state":"AUTO_SELECTED",
                          "report":{"candidate":["momentum",6,0.003,3],
                                    "pairs":["BTC-USD","ETH-USD"]}})
            self.assertEqual(book.step(end=1000*BAR)["status"],"WAITING")
            now[0] += BAR
            self.assertEqual(book.step(end=1001*BAR)["status"],"WAITING")
            now[0] += BAR
            snapshot = book.step(end=1002*BAR)
            self.assertEqual(snapshot["status"], "DATA_UNAVAILABLE")
            self.assertEqual(snapshot["orders"], 0)
            self.assertEqual(snapshot["samples"], 0)

    def test_precreation_and_backfilled_candles_are_not_forward_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            now=[1000*BAR+60]
            book=FastPaperBook(path=Path(td)/"paper.db",
                               fetcher=candles,clock=lambda:now[0])
            self.assertTrue(book.enqueue({
                "id":"no-backfill","ordinal":1,"state":"AUTO_SELECTED",
                "report":{"candidate":["momentum",6,0.003,3],
                          "pairs":["BTC-USD","ETH-USD"]}}))
            self.assertEqual(book.step(end=1000*BAR)["status"],"WAITING")
            self.assertEqual(book.status()["samples"],0)
            with self.assertRaisesRegex(ValueError,"current completed boundary"):
                book.step(end=900*BAR)
            now[0] += BAR
            self.assertEqual(book.step(end=1001*BAR)["status"],"WAITING")
            now[0] += BAR
            first=book.step(end=1002*BAR)
            self.assertEqual(first["status"],"ACTIVE")
            self.assertEqual(first["samples"],1)
            with self.assertRaisesRegex(ValueError,"current completed boundary"):
                book.step(end=1000*BAR)
            self.assertEqual(book.status()["samples"],1)

    def test_clock_rollback_during_fetch_does_not_count_forward_sample(self):
        with tempfile.TemporaryDirectory() as td:
            now=[1000*BAR+60]
            def rollback(product,start,end):
                data=candles(product,start,end)
                now[0]=1001*BAR+1
                return data
            book=FastPaperBook(path=Path(td)/"paper.db",
                               fetcher=rollback,clock=lambda:now[0])
            self.assertTrue(book.enqueue({
                "id":"rollback","ordinal":1,"state":"AUTO_SELECTED",
                "report":{"candidate":["momentum",6,0.003,3],
                          "pairs":["BTC-USD","ETH-USD"]}}))
            now[0] += 2*BAR
            result=book.step(end=1002*BAR)
            self.assertEqual(result["status"],"DATA_UNAVAILABLE")
            self.assertEqual(book.status()["samples"],0)
            self.assertEqual(book.status()["orders"],0)

    def test_slow_fetch_crossing_next_slot_does_not_count_sample(self):
        with tempfile.TemporaryDirectory() as td:
            now=[1000*BAR+60]
            def slow(product,start,end):
                data=candles(product,start,end)
                now[0]=1003*BAR+1
                return data
            book=FastPaperBook(path=Path(td)/"paper.db",
                               fetcher=slow,clock=lambda:now[0])
            self.assertTrue(book.enqueue({
                "id":"slow-fetch","ordinal":1,"state":"AUTO_SELECTED",
                "report":{"candidate":["momentum",6,0.003,3],
                          "pairs":["BTC-USD","ETH-USD"]}}))
            now[0] += 2*BAR
            result=book.step(end=1002*BAR)
            self.assertEqual(result["status"],"DATA_UNAVAILABLE")
            self.assertEqual(book.status()["samples"],0)
            self.assertEqual(book.status()["orders"],0)

    def test_forward_paper_never_turns_into_wallet_action(self):
        with tempfile.TemporaryDirectory() as td:
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=candles, clock=lambda: 1000*BAR+60)
            self.assertEqual(book.status()["status"], "NO_ACTIVE_FORWARD_TRIAL")
            self.assertFalse(book.enqueue({"id":"bad", "state":"REJECTED_BY_EVIDENCE"}))


if __name__ == "__main__":
    unittest.main()
