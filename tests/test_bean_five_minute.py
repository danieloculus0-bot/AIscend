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
            self.assertEqual(first["status"], "ACTIVE")
            self.assertEqual(first["orders"], 0)
            self.assertEqual(first["cash"], 25.0)
            self.assertEqual(first["samples"], 1)
            self.assertEqual(book.step(end=1000*BAR)["status"], "WAITING")
            now[0] += BAR
            second = book.step(end=1001*BAR)
            self.assertEqual(second["status"], "ACTIVE")
            # The candle opened before the first decision was recorded.
            self.assertEqual(second["orders"], 0)
            self.assertIsNone(second["held"])
            now[0] += BAR
            third = book.step(end=1002*BAR)
            self.assertEqual(third["status"], "ACTIVE")
            self.assertEqual(third["orders"], 1)
            self.assertEqual(third["held"], "ETH-USD")
            self.assertGreaterEqual(third["samples"], 3)
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
            with self.assertRaisesRegex(ValueError, "future"):
                book.step(end=1001*BAR)
            with self.assertRaisesRegex(ValueError, "unaligned"):
                book.step(end=1000*BAR+1)
            self.assertEqual(book.status()["samples"],0)
            self.assertEqual(book.status()["orders"],0)
            self.assertEqual(book.step(end=1000*BAR)["samples"],1)

    def test_legacy_pending_without_observation_time_cannot_backfill(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"paper.db"
            now=[1000*BAR+60]
            book=FastPaperBook(path=path,fetcher=candles,clock=lambda:now[0])
            book.enqueue({"id":"legacy","ordinal":1,"state":"AUTO_SELECTED",
                          "report":{"candidate":["momentum",6,0.003,3],
                                    "pairs":["BTC-USD","ETH-USD"]}})
            first=book.step(end=1000*BAR)
            self.assertIsNotNone(first["pending"])
            with closing(sqlite3.connect(path)) as db:
                raw=db.execute("SELECT data FROM paper WHERE id='legacy'").fetchone()[0]
                legacy=json.loads(raw)
                legacy.pop("pending_recorded_at",None)
                db.execute("UPDATE paper SET data=? WHERE id='legacy'",
                           (json.dumps(legacy),))
                db.commit()
            now[0]+=BAR
            second=book.step(end=1001*BAR)
            self.assertEqual(second["orders"],0)
            self.assertIsNone(second["held"])
            self.assertGreater(second["pending_recorded_at"],1000*BAR)

    def test_missing_market_bar_is_not_synthetic_profit(self):
        with tempfile.TemporaryDirectory() as td:
            now = 1000*BAR+60
            def missing(product, start, end):
                data = candles(product, start, end)
                if product == "ETH-USD":
                    data.pop(end-BAR)
                return data
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=missing, clock=lambda: now)
            book.enqueue({"id":"candidate","ordinal":1,"state":"AUTO_SELECTED",
                          "report":{"candidate":["momentum",6,0.003,3],
                                    "pairs":["BTC-USD","ETH-USD"]}})
            snapshot = book.step(end=1000*BAR)
            self.assertEqual(snapshot["status"], "DATA_UNAVAILABLE")
            self.assertEqual(snapshot["orders"], 0)
            self.assertEqual(snapshot["samples"], 0)

    def test_forward_paper_never_turns_into_wallet_action(self):
        with tempfile.TemporaryDirectory() as td:
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=candles, clock=lambda: 1000*BAR+60)
            self.assertEqual(book.status()["status"], "NO_ACTIVE_FORWARD_TRIAL")
            self.assertFalse(book.enqueue({"id":"bad", "state":"REJECTED_BY_EVIDENCE"}))


if __name__ == "__main__":
    unittest.main()
