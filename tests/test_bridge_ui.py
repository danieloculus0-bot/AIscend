from pathlib import Path
import unittest


class BridgeUiTests(unittest.TestCase):
    def test_bridge_controls_and_compact_panels_exist(self):
        html = Path("templates/index.html").read_text(encoding="utf-8")
        self.assertIn("BASE TO ADVANCED", html)
        self.assertIn("ADVANCED TO BASE", html)
        self.assertIn("/api/bridge/base-to-advanced", html)
        self.assertIn("/api/bridge/advanced-to-base", html)
        self.assertIn("decision-scroll", html)
        self.assertIn("<summary>COINBASE ASSET UNIVERSE</summary>", html)
        self.assertIn("<summary>MARKET ARSENAL</summary>", html)
        self.assertIn("<summary>BEAN MEMORY</summary>", html)
        self.assertIn("<summary>HUMAN WEATHER</summary>", html)
        self.assertIn("OPEN MONITOR", html)

    def test_remote_monitor_is_read_only_and_refreshes(self):
        monitor = Path("templates/monitor.html").read_text(encoding="utf-8")
        app = Path("web_app.py").read_text(encoding="utf-8")
        self.assertIn("/api/monitor/status", monitor)
        self.assertIn("setInterval(refresh,5000)", monitor)
        self.assertIn('@app.get("/monitor")', app)
        self.assertIn('@app.get("/api/monitor/status")', app)
        self.assertNotIn("/api/run-once", monitor)
        self.assertNotIn("/api/start", monitor)
        self.assertNotIn("/api/stop", monitor)


if __name__ == "__main__":
    unittest.main()
