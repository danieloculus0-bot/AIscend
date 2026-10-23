"""Deterministic integrity and causality checks for five-year blind runner."""
import csv
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.bean_five_year_blind import (
    START, END_EXCLUSIVE, PRODUCTS, FIELDS, load, signal_state,
)


class FiveYearBlindContracts(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "market.csv"
        days = (END_EXCLUSIVE - START).days
        with self.path.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(FIELDS)
            for p in PRODUCTS:
                for i in range(days):
                    price = (100 if p == PRODUCTS[0] else 150) * (1 + .0001 * i)
                    writer.writerow((p, (START + timedelta(days=i)).isoformat(),
                                     price, price * 1.01, price * .99,
                                     price * 1.001, 10000))
        payload = self.path.read_bytes()
        self.meta = {"csv_sha256": hashlib.sha256(payload).hexdigest(),
                     "rows": days * len(PRODUCTS)}
        self.path.with_suffix(".manifest.json").write_text(
            json.dumps(self.meta))

    def test_complete_five_year_market_index(self):
        dataset, _ = load(self.path)
        self.assertEqual(len(dataset), 2 * (END_EXCLUSIVE - START).days)

    def test_future_prices_never_change_prior_features(self):
        prices, _ = load(self.path)
        decision_day = START + timedelta(days=145)
        before = signal_state(prices, PRODUCTS[0], decision_day)
        for days_later in (1, 10, 100):
            prices[(PRODUCTS[0], decision_day + timedelta(days=days_later))][
                "close"] *= 100
        after = signal_state(prices, PRODUCTS[0], decision_day)
        self.assertEqual(before, after)

    def test_modified_or_truncated_csv_fails_closed(self):
        self.path.write_bytes(self.path.read_bytes() + b"garbage")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            load(self.path)

    def test_missing_session_rejected_even_with_recomputed_hash(self):
        lines = self.path.read_text().splitlines()
        self.path.write_text("\n".join(lines[:-1]) + "\n")
        payload = self.path.read_bytes()
        self.meta["csv_sha256"] = hashlib.sha256(payload).hexdigest()
        self.meta["rows"] -= 1
        self.path.with_suffix(".manifest.json").write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            load(self.path)


if __name__ == "__main__":
    unittest.main()
