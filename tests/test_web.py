import os
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

    def test_remote_monitor_requires_token_and_accepts_valid_token(self):
        client = app.test_client()
        with patch.dict(os.environ, {"AISCEND_REMOTE_TOKEN": "test-remote-token"}):
            denied = client.get(
                "/monitor",
                environ_base={"REMOTE_ADDR": "192.168.1.50"},
            )
            self.assertEqual(denied.status_code, 401)

            allowed = client.get(
                "/monitor?token=test-remote-token",
                environ_base={"REMOTE_ADDR": "192.168.1.50"},
            )
            self.assertEqual(allowed.status_code, 200)
            self.assertIn(b"Read-only", allowed.data)

    def test_remote_monitor_cookie_keeps_refresh_authenticated(self):
        client = app.test_client()
        with patch.dict(os.environ, {"AISCEND_REMOTE_TOKEN": "test-remote-token"}):
            first = client.get(
                "/monitor?token=test-remote-token",
                environ_base={"REMOTE_ADDR": "192.168.1.50"},
            )
            self.assertEqual(first.status_code, 200)
            self.assertIn("aiscend_monitor=", first.headers.get("Set-Cookie", ""))

            refresh = client.get(
                "/monitor",
                environ_base={"REMOTE_ADDR": "192.168.1.50"},
            )
            self.assertEqual(refresh.status_code, 200)

    def test_remote_client_cannot_reach_trade_control_routes(self):
        client = app.test_client()
        with patch.dict(os.environ, {"AISCEND_REMOTE_TOKEN": "test-remote-token"}):
            response = client.post(
                "/api/start",
                json={"network": "base", "rail": "advanced", "interval": 60},
                headers={"X-AIscend-Token": "test-remote-token"},
                environ_base={"REMOTE_ADDR": "192.168.1.50"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn("read-only", response.get_json()["error"].lower())

    def test_remote_client_cannot_spoof_loopback_with_forwarded_header(self):
        client = app.test_client()
        with patch.dict(os.environ, {"AISCEND_REMOTE_TOKEN": "test-remote-token"}):
            response = client.post(
                "/api/start",
                json={"network": "base", "rail": "advanced", "interval": 60},
                headers={
                    "X-AIscend-Token": "test-remote-token",
                    "X-Forwarded-For": "127.0.0.1",
                },
                environ_base={"REMOTE_ADDR": "192.168.1.50"},
            )
        self.assertEqual(response.status_code, 403)

    def test_remote_candle_endpoint_requires_monitor_token(self):
        client = app.test_client()
        with patch.dict(os.environ, {"AISCEND_REMOTE_TOKEN": "test-remote-token"}):
            response = client.get(
                "/api/candles?product=BTC-USD",
                environ_base={"REMOTE_ADDR": "192.168.1.50"},
            )
        self.assertEqual(response.status_code, 401)

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
