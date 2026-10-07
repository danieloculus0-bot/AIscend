import tempfile
import unittest
from pathlib import Path

from src.bean import BeanClaim, BeanMemory
from src.core import StateStore
from web_app import _copy_game_meta


class LinkedRailTests(unittest.TestCase):
    def test_game_baseline_and_clock_follow_capital_between_rails(self):
        with tempfile.TemporaryDirectory() as td:
            base = StateStore(Path(td) / "base.db")
            advanced = StateStore(Path(td) / "advanced.db")
            try:
                base.set_meta("live_starting_value", "25.0000000000")
                base.set_meta("game_started_at", "1234567890.0")
                base.set_meta("milestone_2x_at", "1234567999.0")

                advanced.set_meta("advanced_starting_value", "0.0700000000")
                _copy_game_meta(base, advanced)

                self.assertEqual(
                    advanced.get_meta("advanced_starting_value"),
                    "25.0000000000",
                )
                self.assertEqual(
                    advanced.get_meta("game_started_at"),
                    "1234567890.0",
                )
                self.assertEqual(
                    advanced.get_meta("milestone_2x_at"),
                    "1234567999.0",
                )
            finally:
                base.close()
                advanced.close()

    def test_shared_bean_connection_exposes_same_memory(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "portfolio.db")
            try:
                base_bean = BeanMemory(store.conn)
                advanced_bean = BeanMemory(store.conn)
                base_bean.add_claim(
                    BeanClaim(
                        "OBSERVATION",
                        "TEST",
                        "shared memory survives rail changes",
                        1.0,
                        "unit_test",
                    )
                )
                snap = advanced_bean.snapshot()
                self.assertGreaterEqual(snap["claims"], 1)
            finally:
                store.close()

    def test_dashboard_switches_runner_after_bridge(self):
        html = Path("templates/index.html").read_text(encoding="utf-8")
        self.assertIn('<option value="auto" selected>Linked portfolio - auto choose rail</option>', html)
        self.assertIn("async function switchExecutionRail(target)", html)
        self.assertIn("await fetch('/api/stop'", html)
        self.assertIn("body:JSON.stringify({network:network(),rail:target,interval})", html)
        self.assertIn("j.suggested_rail", html)


if __name__ == "__main__":
    unittest.main()
