from pathlib import Path
import unittest
from research.bean_learning_push_20261024.build_summary import recall_outcomes,recall_checkpoints

class LearningIndexChronologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.database=Path('research/bean_learning_push_20261024/learning_outcomes.sqlite3')
    def test_observed_recall_cannot_backdate_newly_recorded_research(self):
        self.assertEqual(recall_outcomes(self.database,'2025-01-01T00:00:00Z'),[])
        self.assertEqual(recall_checkpoints(self.database,'2025-01-01T00:00:00Z'),[])
    def test_counterfactual_recall_cannot_read_the_evaluation_future(self):
        self.assertEqual(recall_outcomes(self.database,'2023-04-01T00:00:00Z',historical=True),[])
        prior=recall_outcomes(self.database,'2024-01-01T00:00:00Z',historical=True)
        self.assertEqual(len(prior),14);self.assertTrue(all(x['window'].startswith('2023') for x in prior))
        self.assertTrue(any(x['net_return']<0 for x in prior))
    def test_model_recall_respects_training_cutoffs_and_keeps_unqualified_flag(self):
        prior=recall_checkpoints(self.database,'2024-01-01T00:00:00Z',historical=True)
        self.assertEqual(len(prior),7);self.assertTrue(all(x['qualification']=='DEVELOPMENT_ONLY' for x in prior))

if __name__=='__main__':unittest.main()
