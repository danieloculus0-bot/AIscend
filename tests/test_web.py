import unittest

from web_app import _loop_lock, _loop_state, app


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
