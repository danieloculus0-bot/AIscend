import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
import numpy as np
from scripts.collect_strategy_candles import parse_archive,seconds
from scripts.bean_strategy_policy import compare_predictions
from src.bean_strategy_curriculum import ACTIONS,Account,Candles,CandleWorker,Features,PatternMemory,experience,select

def data():
    rng=np.random.default_rng(41);p=100*np.exp(np.cumsum(rng.normal(.001,.02,(440,3)),axis=0));o=p*.995
    return Candles(('BTCUSDT','ETHUSDT','ADAUSDT'),np.arange(440)*86400,{'open':o,'high':p*1.02,'low':o*.98,'close':p,'volume':np.full_like(p,1e6)},'a'*64)

class CurriculumTests(unittest.TestCase):
    def test_portable_prediction_check_requires_same_action_and_bounded_error(self):
        c=compare_predictions({'values':[[1.,2.0000001]]},{'values':[[1.,2.]]})
        self.assertTrue(c['same_greedy_action']);self.assertFalse(c['values_exactly_equal'])
        with self.assertRaises(ValueError):compare_predictions({'values':[[1.,3.]]},{'values':[[1.,2.]]})
        with self.assertRaises(ValueError):compare_predictions({'values':[[1.0000001,1.]]},{'values':[[1.,1.0000001]]})
        with self.assertRaises(ValueError):compare_predictions({'values':[[float('nan'),1.]]},{'values':[[1.,2.]]})
    def test_future_candles_cannot_change_pattern_or_eligibility(self):
        c=data();other=copy.deepcopy(c)
        for v in other.data.values():v[261:]=np.nan
        a,b=Features(c),Features(other)
        self.assertEqual(a.vector(260,0,.5),b.vector(260,0,.5));self.assertEqual(a.targets(260,0,.5),b.targets(260,0,.5));self.assertEqual(a.valid(260,0),b.valid(260,0))
    def test_shared_patterns_do_not_depend_on_price_units(self):
        c=data();scaled=copy.deepcopy(c)
        for k in ('open','high','low','close'):scaled.data[k]*=1000
        np.testing.assert_allclose(Features(c).vector(260,0,.5),Features(scaled).vector(260,0,.5),atol=1e-10)
    def test_retrieval_rejects_future_and_same_symbol(self):
        def row(symbol,end,reward):return {'symbol':symbol,'outcome_at':end,'vector':[0.,0.],'rewards':[reward]*len(ACTIONS)}
        m=PatternMemory([row('a',10,1),row('b',10,2),row('c',100,999)])
        found=m.query([0.,0.],'a',20)
        self.assertEqual(found['count'],1);self.assertEqual(found['mean_rewards'],[2.]*len(ACTIONS));self.assertEqual(found['max_outcome_at'],10)
    def test_flat_market_fees_are_real_and_cash_is_funded(self):
        a=Account(1000);a.trade(1.,100);self.assertGreaterEqual(a.cash,-1e-8);a.trade(0.,100)
        self.assertAlmostEqual(a.cash,1000*.99/1.01);self.assertGreater(a.costs,0)
    def test_floating_point_residue_cannot_make_exposure_exceed_one(self):
        a=Account(-1e-13,.3)
        self.assertEqual(a.exposure(3.),1.)
        a.trade(a.exposure(3.),3.)
        self.assertGreaterEqual(a.cash,-1e-8);self.assertLessEqual(a.exposure(3.),1.)
    def test_teaching_reward_uses_delayed_fill_and_matured_outcome(self):
        c=data()
        for k in ('open','high','low','close'):c.data[k][:]=100
        x=experience(Features(c),260,0,0.,ACTIONS.index('buy_hold'))
        self.assertGreater(x['outcome_at'],x['decision_at']);self.assertLess(x['reward'],0)
        self.assertEqual(experience(Features(c),260,0,0.,0)['reward'],0.)
    def test_selection_ignores_later_audit_and_rewards_consistent_folds(self):
        a={'seed':7,'budget':256,'fold_returns':[.02,.03],'audit':-100};b={'seed':41,'budget':1024,'fold_returns':[.5,-.01],'audit':100}
        self.assertEqual(select([a,b]),a);a['audit'],b['audit']=1e6,-1e6;self.assertEqual(select([a,b]),a)
    def test_official_archive_microseconds_are_normalized(self):
        self.assertEqual(seconds('1735689600000000'),1735689600);self.assertEqual(seconds('1640995200000'),1640995200)
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('x.csv','1735689600000000,100,110,90,105,12,1735775999999999,1234,9,1,1,0\n')
        row=parse_archive('BTCUSDT',b.getvalue())[0];self.assertEqual(row['available_at'],1735776000);self.assertEqual(row['high'],110)
    @unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'),'Explicit actual BEAN required')
    def test_actual_neural_learner_rejects_unmatured_teaching_and_retains_gradients(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'model.sqlite3';core=Path(os.environ['BEAN_CORE_DIR']);worker=CandleWorker(core,path,4,7,100)
            try:
                original=worker.call('status')
                with self.assertRaisesRegex(RuntimeError,'future teaching'):worker.call('teach',experiences=[{'decision_at':100,'outcome_at':101,'vector':[0.]*4,'action':0,'reward':100.}])
                self.assertEqual(worker.call('status'),original)
                worker.call('teach',experiences=[{'decision_at':10,'outcome_at':20,'vector':[i/100.,0.,0.,0.],'action':i%len(ACTIONS),'reward':float(i%3)} for i in range(128)])
                worker.call('train',updates=4);learned=worker.call('status');self.assertNotEqual(learned['weights_sha256'],original['weights_sha256'])
                worker.call('checkpoint',score=-1.,samples=128,evaluation_sha256='a'*64,episode='test',protocol_sha256='b'*64)
            finally:worker.close()
            restored=CandleWorker(core,path,4,7,100,restore=True)
            try:
                self.assertEqual(restored.call('status'),learned)
                with self.assertRaisesRegex(RuntimeError,'later knowledge'):restored.call('values',vectors=[[0.]*4],decision_at=99)
            finally:restored.close()

if __name__=='__main__':unittest.main()
