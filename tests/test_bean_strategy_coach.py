import copy
import os
from pathlib import Path
import tempfile
import unittest
from src.bean_strategy_coach import CoachWorker,mentor_lesson,summaries,run
from src.bean_mentor_worker import validate_lesson
from src.bean_training_batches import digest_chunks
from src.bean_strategy_curriculum import ACTIONS,Features
from src.strategy_atlas import digest
from scripts.consolidate_bean_mentor_feedback import matched_comparison
from tests.test_bean_strategy_curriculum import data

class ReceiptWorker:
    """Checks that every taught cohort is followed by optimizer updates."""
    def __init__(self, transitions_per_item):
        self.transitions_per_item = transitions_per_item
        self.frames = 0
        self.updates = 0
        self.calls = []
        self.first_digest_at = None
        self.awaiting_train = False

    def call(self, operation, **arguments):
        if operation == 'status':
            return {'frames': self.frames, 'updates': self.updates}
        if operation == 'teach':
            if self.awaiting_train:
                raise AssertionError('Next cohort evicted untrained lessons')
            batch = arguments.get('lessons', arguments.get('experiences'))
            self.frames += len(batch) * self.transitions_per_item
            self.calls.append(('teach', len(batch), self.frames))
            self.awaiting_train = True
            return {'frames': self.frames}
        if operation == 'train':
            if not self.awaiting_train or self.frames < 64:
                raise AssertionError('Optimizer did not receive a full cohort')
            target = arguments['updates']
            if target <= self.updates:
                raise AssertionError('Expected new gradient updates after each cohort')
            self.updates = target
            self.calls.append(('train', self.updates, self.frames))
            if self.first_digest_at is None:
                self.first_digest_at = self.frames
            self.awaiting_train = False
            return {'updates': self.updates}
        raise AssertionError(operation)


class StrategyCoachTests(unittest.TestCase):
    def test_reproducibility_controls_reject_invalid_seeds_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for seed in (-1, 2**32, 1.5, True):
                with self.subTest(seed=seed), self.assertRaises(ValueError):
                    run(d / 'unused.gz', d / 'missingcore', d / 'results', seed=seed)
                self.assertFalse((d / 'results').exists())
            for batch in (0, 5, 257, True):
                with self.subTest(batch=batch), self.assertRaises(ValueError):
                    run(d / 'unused.gz', d / 'missingcore', d / 'results',
                        batch_lessons=batch)
                self.assertFalse((d / 'results').exists())

    @unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'), 'Actual pinned BEAN Core required')
    def test_different_seeds_initialize_distinct_real_core_models(self):
        f = Features(data())
        lessons = [mentor_lesson(f, i, 0, .5) for i in range(260, 324, 8)]
        with tempfile.TemporaryDirectory() as temp:
            statuses = []
            for seed in (7, 73):
                d = Path(temp) / str(seed)
                d.mkdir()
                w = CoachWorker(Path(os.environ['BEAN_CORE_DIR']),
                                d / 'model.sqlite3', len(lessons[0]['vector']),
                                10**10, 'coached_advantage', summaries(lessons), d,
                                seed=seed)
                try:
                    statuses.append(w.call('status'))
                finally:
                    w.close()
            self.assertNotEqual(statuses[0]['weights_sha256'],
                                statuses[1]['weights_sha256'])

    def test_all_mentor_chunks_are_digested_before_replay_eviction(self):
        # 47,520 observations cannot fit in BEAN's 4,096-transition replay.
        # Every cohort must train before the next cohort is accepted.
        order = list(range(4320))
        worker = ReceiptWorker(len(ACTIONS))
        receipt = digest_chunks(worker, order, field='lessons', updates=1024,
                                batch_size=32, transitions_per_item=len(ACTIONS))
        self.assertEqual(receipt['transitions'], 47520)
        self.assertEqual(receipt['gradient_updates'], 1024)
        self.assertEqual(worker.first_digest_at, 32 * len(ACTIONS))
        self.assertLess(worker.first_digest_at, 4096)
        self.assertEqual(len(worker.calls), 2 * receipt['cohort_count'])
        self.assertEqual([x[0] for x in worker.calls], ['teach', 'train'] * receipt['cohort_count'])
        self.assertEqual(receipt['receipts'][-1]['updates_through'], 1024)

    def test_matched_coaching_arms_get_identical_digestion_budgets(self):
        sequence = list(range(4320))
        a, b = ReceiptWorker(len(ACTIONS)), ReceiptWorker(len(ACTIONS))
        left = digest_chunks(a, sequence, field='lessons', updates=1024,
                             batch_size=32, transitions_per_item=len(ACTIONS))
        right = digest_chunks(b, sequence, field='lessons', updates=1024,
                              batch_size=32, transitions_per_item=len(ACTIONS))
        self.assertEqual(left, right)
        self.assertEqual(a.calls, b.calls)

    def test_digest_rejects_cohort_bigger_than_replay_without_teaching(self):
        worker = ReceiptWorker(len(ACTIONS))
        with self.assertRaisesRegex(ValueError, 'Replay budget'):
            digest_chunks(worker, list(range(10000)), field='lessons',
                          updates=1, batch_size=32, transitions_per_item=len(ACTIONS))
        self.assertEqual(worker.calls, [])

    @unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'), 'Actual pinned BEAN Core required')
    def test_real_core_trains_while_earlier_mentor_examples_remain_present(self):
        f = Features(data())
        lessons = [mentor_lesson(f, i, 0, .5) for i in range(260, 356, 8)]
        with tempfile.TemporaryDirectory() as temp:
            worker = CoachWorker(Path(os.environ['BEAN_CORE_DIR']),
                                 Path(temp) / 'chunked.sqlite3',
                                 len(lessons[0]['vector']), 10**10,
                                 'coached_advantage', summaries(lessons), temp)
            try:
                original = worker.call('status')
                receipt = digest_chunks(worker, lessons, field='lessons', updates=6,
                                        batch_size=6, transitions_per_item=len(ACTIONS))
                learned = worker.call('status')
                self.assertEqual(receipt['cohort_count'], 2)
                self.assertEqual(learned['updates'], 6)
                self.assertEqual(learned['frames'], len(lessons) * len(ACTIONS))
                self.assertNotEqual(learned['weights_sha256'], original['weights_sha256'])
            finally:
                worker.close()

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
