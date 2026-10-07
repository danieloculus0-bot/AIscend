from pathlib import Path
import unittest


class TamagotchiTests(unittest.TestCase):
    def test_original_body_and_new_unhappy_behavior_are_present(self):
        html = Path("templates/index.html").read_text(encoding="utf-8")
        self.assertIn("line(6,11,2);", html)
        self.assertIn("if((research.prediction==='BEARISH' || score <= -0.16)", html)
        self.assertIn("taking a tactical shit", html)
        self.assertIn("market looks like ass", html)
        self.assertIn("FUCK YEAH. A POINT.", html)


if __name__ == "__main__":
    unittest.main()
