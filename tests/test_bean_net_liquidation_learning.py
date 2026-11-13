import copy
import unittest
from src.bean_economic_experiment import examples
from src.bean_net_liquidation_learning import relabel
from src.bean_strategy_curriculum import Features
from tests.test_bean_strategy_curriculum import data

class LiquidationLearningTests(unittest.TestCase):
    def test_every_source_case_reconciles_and_preserves_input_order(self):
        f=Features(data());raw,market=examples(f,10**10);new=relabel(raw,market,f.c.symbols)
        self.assertEqual([x['source_id'] for x in new],[x['id'] for x in raw]);self.assertEqual(len(new),len(raw))
        self.assertTrue(any(abs(x['reward']-x['old_reward'])>1e-5 for x in new))
        self.assertEqual(new[0]['vector'],raw[0]['vector']);self.assertEqual(new[0]['outcome_at'],raw[0]['outcome_at'])
    def test_missing_or_changed_individual_evidence_cannot_be_invented(self):
        f=Features(data());raw,market=examples(f,10**10)
        with self.assertRaises((ValueError,KeyError)):relabel(raw[1:],market,f.c.symbols)
        changed=copy.deepcopy(raw);changed[0]['reward']+=1.
        with self.assertRaisesRegex(ValueError,'reconcile'):relabel(changed,market,f.c.symbols)

if __name__=='__main__':unittest.main()
