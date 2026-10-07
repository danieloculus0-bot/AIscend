import tempfile
import unittest
from pathlib import Path

from src.core import StateStore
from src.game import score_game


class GameScoreTests(unittest.TestCase):
    def make_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = StateStore(Path(temp.name) / "game.db")
        self.addCleanup(store.close)
        return store

    def test_start_is_zero_points(self):
        store = self.make_store()
        score = score_game(store, 25.0, 25.0)
        self.assertAlmostEqual(score.multiple, 1.0)
        self.assertEqual(score.points, 0)
        self.assertEqual(score.next_milestone_multiple, 2.0)

    def test_one_completed_double_is_one_point(self):
        store = self.make_store()
        score = score_game(store, 50.0, 25.0)
        self.assertEqual(score.points, 1)
        self.assertEqual(score.next_milestone_multiple, 4.0)

    def test_points_only_increment_on_completed_doubles(self):
        store = self.make_store()
        self.assertEqual(score_game(store, 99.99, 25.0).points, 1)
        self.assertEqual(score_game(store, 100.0, 25.0).points, 2)
        self.assertEqual(score_game(store, 200.0, 25.0).points, 3)


if __name__ == "__main__":
    unittest.main()
