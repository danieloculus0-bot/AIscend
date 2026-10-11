from pathlib import Path
import unittest


class BeanMascotTests(unittest.TestCase):
    def test_gameboy_bean_and_existing_mood_behavior_are_present(self):
        html = Path("templates/index.html").read_text(encoding="utf-8")
        self.assertIn("const unit=4, size=48;", html)
        self.assertIn("oval(19,26,2.7,3.2,color.ink);", html)
        self.assertIn("oval(30,26,2.7,3.2,color.ink);", html)
        self.assertIn("const waving=", html)
        self.assertIn("if((research.prediction==='BEARISH' || score <= -0.16)", html)
        self.assertIn("taking a tactical shit", html)
        self.assertIn("market looks like ass", html)
        self.assertIn("FUCK YEAH. A POINT.", html)
        self.assertIn("bean:'BEAN · LEARNING'", html)
        self.assertIn("trouble:'BEAN · TROUBLESHOOTER'", html)
        self.assertIn("trade-shaped object detected", html)
        self.assertIn("confidence >= 0.30", html)
        self.assertNotIn("TRADER THING", html)


if __name__ == "__main__":
    unittest.main()
