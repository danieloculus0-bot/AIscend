import copy
import os
from pathlib import Path
import tempfile
import unittest
from src.bean_strategy_coach import CoachWorker,mentor_lesson,summaries
from src.bean_mentor_worker import validate_lesson
from src.bean_strategy_curriculum import ACTIONS,Features
from src.strategy_atlas import digest
from scripts.consolidate_bean_mentor_feedback import matched_comparison
from tests.test_bean_strategy_curriculum import data

class StrategyCoachTests(unittest.TestCase):
    def test_profit_and_loss_reconcile_market_moves_and_costs(self):
        f=Features(data())
        for exposure in (0.,.5,1.):
            lesson=mentor_lesson(f,260,0,exposure);validate_lesson(lesson,10**10)
            self.assertEqual(len(lesson['cases']),len(ACTIONS))
            hold=lesson['cases'][1]
            for c in lesson['cases']:
                self.assertAlmostEqual(c['market_pnl_before_fill']+c['market_pnl_after_fill']-c['paid_cost'],c['net_return'])
                self.assertAlmostEqual((c['market_pnl_before_fill']+c['market_pnl_after_fill']-hold['market_pnl_before_fill']-hold['market_pnl_after_fill'])-(c['paid_cost']-hold['paid_cost']),c['net_return']-hold['net_return'])
    def test_flat_market_teacher_explains_avoided_fees(self):
        c=data()
        for k in ('open','high','low','close'):c.data[k][:]=100
        lesson=mentor_lesson(Features(c),260,0,0.)
        hold=lesson['cases'][1];buy=lesson['cases'][2]
        self.assertEqual(hold['paid_cost'],0);self.assertEqual(hold['net_return'],0)
        self.assertAlmostEqual(buy['market_pnl_before_fill']+buy['market_pnl_after_fill'],0)
        self.assertAlmostEqual(buy['net_return'],-buy['paid_cost']);self.assertLess(buy['advantage_over_hold'],0)
        receipt=matched_comparison(lesson,'buy_hold')
        self.assertIn('F02_MENTOR_PAID_LESS',receipt['reason_codes'])
        self.assertAlmostEqual(receipt['net_advantage'],-buy['net_return'])
    def test_future_outcome_is_a_label_and_never_a_decision_feature(self):
        c=data();changed=copy.deepcopy(c)
        for k in ('open','high','low','close'):changed.data[k][261:]*=2
        a,b=mentor_lesson(Features(c),260,0,1.),mentor_lesson(Features(changed),260,0,1.)
        self.assertEqual(a['vector'],b['vector']);self.assertNotEqual(a['cases'][1]['raw_log_reward'],b['cases'][1]['raw_log_reward'])
    def test_future_changed_and_unreconciled_explanations_are_rejected(self):
        lesson=mentor_lesson(Features(data()),260,0,.5)
        with self.assertRaisesRegex(ValueError,'Future'):validate_lesson(lesson,lesson['decision_at'])
        changed=copy.deepcopy(lesson);changed['cases'][0]['net_return']+=.1
        with self.assertRaisesRegex(ValueError,'changed'):validate_lesson(changed,10**10)
        changed['id']=digest({k:v for k,v in changed.items() if k!='id'})
        with self.assertRaisesRegex(ValueError,'reconcile'):validate_lesson(changed,10**10)
    @unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'),'Actual pinned BEAN Core required')
    def test_actual_bean_consumes_comparisons_once_and_updates_parameters(self):
        f=Features(data());lessons=[mentor_lesson(f,i,0,.5) for i in range(260,324,8)];cutoff=10**10
        with tempfile.TemporaryDirectory() as temp:
            worker=CoachWorker(Path(os.environ['BEAN_CORE_DIR']),Path(temp)/'policy.sqlite3',len(lessons[0]['vector']),cutoff,'coached_advantage',summaries(lessons),temp)
            try:
                initial=worker.call('status');receipt=worker.call('teach',lessons=lessons)
                self.assertEqual(receipt['lessons'],8);self.assertEqual(receipt['frames'],8*len(ACTIONS))
                self.assertEqual(receipt['receipts'][0]['used_rewards'],[c['advantage_over_hold'] for c in lessons[0]['cases']])
                with self.assertRaisesRegex(RuntimeError,'Duplicated'):worker.call('teach',lessons=[lessons[0]])
                self.assertEqual(worker.call('status')['frames'],8*len(ACTIONS))
                worker.call('train',updates=4);self.assertNotEqual(worker.call('status')['weights_sha256'],initial['weights_sha256'])
                prediction=worker.call('values',vectors=[lessons[0]['vector']],decision_at=cutoff)
                self.assertTrue(prediction['explanation']['past_comparison_ids']);self.assertLessEqual(prediction['explanation']['latest_supporting_outcome'],cutoff)
            finally:worker.close()

if __name__=='__main__':unittest.main()
