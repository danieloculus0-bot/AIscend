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

    def test_rebalance_cadence_preserves_delayed_signal_fills(self):
        """The frozen 3-bar rebalance gene must not trade every five minutes."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"paper.db"
            now = [1000*BAR+60]
            selected = {
                "id": "rebalance", "ordinal": 1, "state": "AUTO_SELECTED",
                "report": {"candidate": ["momentum", 6, 0.003, 3],
                           "pairs": ["BTC-USD", "ETH-USD"]}}
            book = FastPaperBook(path=path, fetcher=candles,
                                 clock=lambda: now[0])
            self.assertTrue(book.enqueue(selected))
            with patch("src.bean_five_minute._choose",
                       side_effect=["ETH-USD", None]) as chooser:
                for tick in (1000, 1001):
                    now[0] = tick*BAR+60
                    self.assertEqual(book.step(end=tick*BAR)["status"], "WAITING")
                for tick in (1002, 1003):
                    now[0] = tick*BAR+60
                    result = book.step(end=tick*BAR)
                    self.assertEqual(result["orders"], 0)
                self.assertEqual(chooser.call_count, 1)
                # A restart must retain the signal until its eligible open.
                book = FastPaperBook(path=path, fetcher=candles,
                                     clock=lambda: now[0])
                now[0] = 1004*BAR+60
                entered = book.step(end=1004*BAR)
                self.assertEqual(entered["orders"], 1)
                self.assertEqual(entered["held"], "ETH-USD")
                self.assertEqual(chooser.call_count, 1)
                now[0] = 1005*BAR+60
                changed = book.step(end=1005*BAR)
                self.assertEqual(changed["orders"], 1)
                self.assertIsNone(changed["pending"])
                self.assertEqual(chooser.call_count, 2)
                for tick in (1006, 1007):
                    now[0] = tick*BAR+60
                    result = book.step(end=tick*BAR)
                self.assertEqual(result["orders"], 2)
                self.assertIsNone(result["held"])
                self.assertEqual(chooser.call_count, 2)

    def test_persistent_candidate_retains_incumbent_as_in_simulate(self):
        """A persistent gene cannot liquidate on a transient alternate pick."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            now = [1000*BAR+60]
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=candles, clock=lambda: now[0])
            self.assertTrue(book.enqueue({
                "id": "persistent", "ordinal": 1, "state": "AUTO_SELECTED",
                "report": {"candidate": ["persistent", 6, 0.003, 3],
                           "pairs": ["BTC-USD", "ETH-USD"]}}))
            with patch("src.bean_five_minute._choose",
                       side_effect=["ETH-USD", None]) as chooser:
                for tick in range(1000, 1008):
                    now[0] = tick*BAR+60
                    result = book.step(end=tick*BAR)
            self.assertEqual(chooser.call_count, 2)
            self.assertEqual(result["orders"], 1)
            self.assertEqual(result["held"], "ETH-USD")
            self.assertEqual(result["pending"], "ETH-USD")

    def test_reject_invalid_frozen_strategy_gene(self):
        with tempfile.TemporaryDirectory() as td:
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=candles, clock=lambda: 1000*BAR+60)
            for candidate in (["momentum", 6, 0.003, 0],
                              ["unknown", 6, 0.003, 3],
                              ["momentum", 6, 0.003, 1]):
                self.assertFalse(book.enqueue({
                    "id": str(candidate), "state": "AUTO_SELECTED",
                    "report": {"candidate": candidate,
                               "pairs": ["BTC-USD", "ETH-USD"]}}))

    def test_changed_or_legacy_source_cannot_reuse_forward_trial(self):
        """Never mix paper evidence from different source implementations."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"paper.db"
            now = [1000*BAR+60]
            book = FastPaperBook(path=path, fetcher=candles,
                                 clock=lambda: now[0])
            selected = {"id": "source-v1", "ordinal": 1,
                        "state": "AUTO_SELECTED",
                        "report": {"candidate": ["momentum", 6, 0.003, 3],
                                   "pairs": ["BTC-USD", "ETH-USD"]}}
            self.assertTrue(book.enqueue(selected))
            self.assertEqual(len(book.status()["source_sha"]), 64)
            with patch("src.bean_five_minute._paper_source_sha",
                       return_value="0"*64):
                changed = book.step(end=1000*BAR)
            self.assertEqual(changed["status"], "INVALIDATED_SOURCE")
            self.assertEqual(changed["samples"], 0)
            self.assertEqual(changed["orders"], 0)
            self.assertEqual(book.status()["status"], "INVALIDATED_SOURCE")

            # A previously finished verdict must not appear to validate
            # the current revision after source code changes.
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE paper SET state='VALIDATED_FORWARD_PAPER' "
                    "WHERE id='source-v1'")
                db.commit()
            with patch("src.bean_five_minute._paper_source_sha",
                       return_value="0"*64):
                stale = book.status()
            self.assertEqual(stale["status"], "INVALIDATED_SOURCE")
            self.assertEqual(stale["historical_status"],
                             "VALIDATED_FORWARD_PAPER")

            # Pre-fingerprint trials are also untrusted, not silently
            # migrated into a ledger governed by different trading rules.
            # Distinct update times make status() ordering deterministic.
            now[0] += 1
            selected["id"] = "legacy-source"
            self.assertTrue(book.enqueue(selected))
            with closing(sqlite3.connect(path)) as db:
                record = json.loads(db.execute(
                    "SELECT data FROM paper WHERE id='legacy-source'"
                ).fetchone()[0])
                record.pop("source_sha")
                db.execute("UPDATE paper SET data=? WHERE id='legacy-source'",
                           (json.dumps(record),))
                db.commit()
            legacy = book.step(end=1000*BAR)
            self.assertEqual(legacy["status"], "INVALIDATED_SOURCE")
            self.assertEqual(legacy["samples"], 0)
            self.assertEqual(book.status()["status"], "INVALIDATED_SOURCE")

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
                legacy.pop("signal_queue",None)
                db.execute("UPDATE paper SET data=? WHERE id='legacy'",
                           (json.dumps(legacy),))
                db.commit()
            for tick in (1003, 1004, 1005):
                now[0] = tick*BAR+60
                second = book.step(end=tick*BAR)
            self.assertEqual(second["orders"], 0)
            self.assertIsNone(second["held"])
            self.assertGreater(second["pending_recorded_at"], 1005*BAR)

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

    def test_nonfinite_paper_equity_cannot_be_persisted_as_forward_profit(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            now = [1000*BAR+60]
            def extreme(product, start, end):
                data = candles(product, start, end)
                if product == "ETH-USD" and 1003*BAR in data:
                    # Finite input prices, but quantity times close overflows.
                    data[1003*BAR] = {"open": 1e-300, "close": 1e300}
                return data
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=extreme, clock=lambda: now[0])
            self.assertTrue(book.enqueue({
                "id": "overflow", "ordinal": 1, "state": "AUTO_SELECTED",
                "report": {"candidate": ["momentum",6,0.003,3],
                           "pairs": ["BTC-USD", "ETH-USD"]}}))
            with patch("src.bean_five_minute._choose", return_value="ETH-USD"):
                for tick in (1000, 1001, 1002, 1003):
                    now[0] = tick*BAR+60
                    book.step(end=tick*BAR)
                now[0] = 1004*BAR+60
                result = book.step(end=1004*BAR)
            self.assertEqual(result["status"], "DATA_UNAVAILABLE")
            self.assertIn("Nonfinite forward paper accounting",
                          result["last_error"])
            persisted = book.status()
            self.assertEqual(persisted["status"], "ACTIVE")
            self.assertEqual(persisted["orders"], 0)
            self.assertEqual(persisted["samples"], 2)
            self.assertEqual(persisted["equity"], 25.0)
            json.dumps(persisted, allow_nan=False)

    def test_nonfinite_benchmark_ratio_cannot_be_persisted(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            now = [1000*BAR+60]
            def extreme(product, start, end):
                data = candles(product, start, end)
                if product == "BTC-USD":
                    for tick, price in ((1001, 1e-300), (1002, 1e300)):
                        if tick*BAR in data:
                            data[tick*BAR] = {"open": price, "close": price}
                return data
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=extreme, clock=lambda: now[0])
            self.assertTrue(book.enqueue({
                "id": "benchmark-overflow", "ordinal": 1,
                "state": "AUTO_SELECTED",
                "report": {"candidate": ["momentum",6,0.003,3],
                           "pairs": ["BTC-USD", "ETH-USD"]}}))
            with patch("src.bean_five_minute._choose", return_value=None):
                now[0] = 1002*BAR+60
                self.assertEqual(book.step(end=1002*BAR)["samples"], 1)
                now[0] = 1003*BAR+60
                failed = book.step(end=1003*BAR)
            self.assertEqual(failed["status"], "DATA_UNAVAILABLE")
            self.assertIn("Nonfinite forward paper accounting",
                          failed["last_error"])
            persisted = book.status()
            self.assertEqual(persisted["samples"], 1)
            self.assertEqual(persisted["orders"], 0)
            json.dumps(persisted, allow_nan=False)

    def test_forward_paper_never_turns_into_wallet_action(self):
        with tempfile.TemporaryDirectory() as td:
            book = FastPaperBook(path=Path(td)/"paper.db",
                                 fetcher=candles, clock=lambda: 1000*BAR+60)
            self.assertEqual(book.status()["status"], "NO_ACTIVE_FORWARD_TRIAL")
            self.assertFalse(book.enqueue({"id":"bad", "state":"REJECTED_BY_EVIDENCE"}))


if __name__ == "__main__":
    unittest.main()
