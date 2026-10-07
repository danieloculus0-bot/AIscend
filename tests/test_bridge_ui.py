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


if __name__ == "__main__":
    unittest.main()
