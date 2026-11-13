import unittest
from src.bean_full_pass_learning import full_pass_budget

class FullPassBudgetTests(unittest.TestCase):
    def test_budget_must_cover_the_complete_evidence_at_least_once(self):
        plan=full_pass_budget(47520,1024)
        self.assertEqual(plan['minimum_batches'],743)
        with self.assertRaises(ValueError):full_pass_budget(47520,256)
        with self.assertRaises(ValueError):full_pass_budget(32,1024)

if __name__=='__main__':unittest.main()
