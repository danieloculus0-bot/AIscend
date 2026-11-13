"""Prevent scheduled public-data research from silently reverting to stale fee assumptions.

Research only. No network calls, credentials, wallets or order submissions.
"""
import inspect
import json
from pathlib import Path
import re
import unittest

from src import strategy_research, universe_research


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "aiscend-strategy-research.yml": "python -m src.strategy_research",
    "coinbase-universe-research.yml": "python -m src.universe_research",
}


class ScheduledResearchFeeBaselineTests(unittest.TestCase):
    def test_both_scheduled_research_workflows_explicitly_use_90bps(self):
        for name, command in WORKFLOWS.items():
            with self.subTest(workflow=name):
                source = (ROOT / ".github" / "workflows" / name).read_text(
                    encoding="utf-8"
                )
                self.assertIn("schedule:", source)
                self.assertIn(command, source)
                fees = re.findall(r"--fee-bps\s+(\d+(?:\.\d+)?)", source)
                self.assertEqual([float(value) for value in fees], [90.0])

    def test_scheduled_fee_agrees_with_python_defaults_and_frozen_paper_model(self):
        self.assertEqual(
            inspect.signature(strategy_research.validate)
            .parameters["fee_bps"].default, 90.0
        )
        self.assertEqual(
            inspect.signature(universe_research.run_shard)
            .parameters["fee_bps"].default, 90.0
        )
        manifest = json.loads(
            (ROOT / "research" / "bean_frozen_prospective_20261023_fee90.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["fee_bps_per_side"], 90)
        self.assertEqual(manifest["spread_bps_roundtrip"], 10)
        self.assertFalse(manifest["promotion_authorized"])


if __name__ == "__main__":
    unittest.main()
