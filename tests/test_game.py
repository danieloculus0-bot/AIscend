import tempfile
import unittest
from pathlib import Path

from src.core import StateStore
from src.game import score_game, set_win_target


class GameScoreTests(unittest.TestCase):
    def make_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = StateStore(Path(temp.name) / "game.db")
        self.addCleanup(store.close)
        return store

    def test_start_is_near_zero_points(self):
        store = self.make_store()
        score = score_game(store, 25.0, 25.0)
        self.assertAlmostEqual(score.multiple, 1.0)
        self.assertAlmostEqual(score.points, 0.0, places=2)

    def test_doubling_is_about_one_thousand_points(self):
        store = self.make_store()
        score = score_game(store, 50.0, 25.0)
        self.assertAlmostEqual(score.multiple, 2.0)
        self.assertGreater(score.points, 999.0)

    def test_target_triggers_victory(self):
        store = self.make_store()
        set_win_target(store, 1000)
        score = score_game(store, 1000, 25)
        self.assertTrue(score.victory)
        self.assertEqual(score.target_progress, 1.0)


if __name__ == "__main__":
    unittest.main()
