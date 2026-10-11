import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core import StateStore
from web_app import _loop_lock, _loop_state, _run_once, app


class WebAppTests(unittest.TestCase):
    def tearDown(self):
        with _loop_lock:
            _loop_state.update(
                {
                    "running": False,
                    "rail": "base",
                    "network": "base",
                    "interval": 60.0,
                    "last_error": None,
                }
            )

    def test_index_loads(self):
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"AIscend", response.data)

    def test_failed_cycle_is_written_to_decision_journal(self):
        class BrokenEngine:
            def step(self):
                raise RuntimeError("market discovery exploded")

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "advanced.db"
            store = StateStore(db_path)
            with patch("web_app._make_engine", return_value=(store, BrokenEngine())):
                with self.assertRaisesRegex(RuntimeError, "market discovery exploded"):
                    _run_once("base", "advanced")

            check = StateStore(db_path)
            try:
                row = check.latest_decisions(1)[0]
                self.assertEqual(row["status"], "CYCLE_ERROR")
                self.assertIn("market discovery exploded", row["rationale"])
            finally:
                check.close()

    def test_remote_monitor_requires_token_and_is_read_only(self):
        client = app.test_client()
        remote = {"REMOTE_ADDR": "100.101.102.103"}
        with patch.dict("os.environ", {"AISCEND_REMOTE_TOKEN": "test-only-secret"}):
            self.assertEqual(
                client.get("/monitor", environ_overrides=remote).status_code, 401
            )
            self.assertEqual(
                client.get("/monitor?token=test-only-secret",
                           environ_overrides=remote).status_code, 200
            )
            self.assertEqual(
                client.get("/api/advanced-status",
                           environ_overrides=remote).status_code, 401
            )
            self.assertEqual(
                client.post("/api/start", json={"rail": "advanced"},
                            environ_overrides=remote).status_code, 403
            )

    def test_remote_monitor_fails_closed_without_configured_token(self):
        client = app.test_client()
        with patch.dict("os.environ", {"AISCEND_REMOTE_TOKEN": ""}):
            self.assertEqual(
                client.get("/monitor", environ_overrides={
                    "REMOTE_ADDR": "100.101.102.103"}).status_code,
                401,
            )

    def test_bean_lab_endpoint_requires_local_access_and_does_not_trade(self):
        class FakeLab:
            def status(self):
                return {"running": False, "latest": None, "history": [], "retained_events": 0}
            def start(self):
                return {"running": True, "latest": {"id": "test-1"}, "history": []}
            def approve(self, event_id):
                if event_id != "test-1":
                    raise ValueError("No qualified research candidate")
                return {"approved": True}
        with patch("web_app._bean_lab", FakeLab()):
            client = app.test_client()
            self.assertEqual(client.get("/api/bean-lab").get_json()["retained_events"], 0)
            start = client.post("/api/bean-lab/start")
            self.assertEqual(start.status_code, 202)
            self.assertEqual(start.get_json()["latest"]["id"], "test-1")
            self.assertEqual(client.post("/api/bean-lab/approve",
                                         json={"event_id": "missing"}).status_code, 409)
            self.assertEqual(client.post("/api/bean-lab/approve",
                                         json={"event_id": "test-1"}).get_json()["approved"], True)
            remote = {"REMOTE_ADDR": "100.101.102.103"}
            self.assertEqual(client.post("/api/bean-lab/start",
                                         environ_overrides=remote).status_code, 403)

    def test_homepage_exposes_explicit_research_only_controls(self):
        body = app.test_client().get("/").data
        self.assertIn(b"BEAN STRATEGY SCHOOL", body)
        self.assertIn(b"TRY AGAIN", body)
        self.assertIn(b"APPROVE CANDIDATE FOR REVIEW", body)

    def test_start_refuses_silent_rail_switch_while_runner_is_active(self):
        with _loop_lock:
            _loop_state.update(
                {
                    "running": True,
                    "rail": "base",
                    "network": "base",
                    "interval": 60.0,
                    "last_error": None,
                }
            )

        client = app.test_client()
        response = client.post(
            "/api/start",
            json={"network": "base", "rail": "advanced", "interval": 60},
        )
        self.assertEqual(response.status_code, 409)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("already active", data["error"])


if __name__ == "__main__":
    unittest.main()
