import copy
import math
import os
from pathlib import Path
import tempfile
import unittest
from src.bean_strategy_curriculum import ACTIONS,Features,experience
from src.bean_economic_values import funded_values,market_vector,choose,gate_overrides
from src.bean_economic_experiment import Worker,examples,recover,best_teacher,evaluate
from src.bean_causal_simulation import DecisionLedger
from tests.test_bean_strategy_curriculum import data

class EconomicValueTests(unittest.TestCase):
    def test_holding_cash_and_cash_are_identical_for_any_forecast(self):
        f=Features(data());v=f.vector(260,0,0.)
        for forecast in ((10.,30.),(-30.,-20.),(0.,0.)):
            values=funded_values(v,forecast)
            self.assertEqual(values[0]['net_log_return'],0.)
            self.assertEqual(values[1]['net_log_return'],0.)
    def test_identical_target_exposures_share_economic_values(self):
        v=Features(data()).vector(260,0,.5);v[36:]=[.25]*len(ACTIONS)
        r=funded_values(v,(2.,-4.))
        self.assertEqual(len({x['net_log_return'] for i,x in enumerate(r) if i!=1}),1)
    def test_funded_values_match_realized_account_math_with_known_test_path(self):
        c=data();f=Features(c);i,j=260,0
        forecast=[100*math.log(c.data['open'][i+2,j]/c.data['close'][i,j]),100*math.log(c.data['close'][i+8,j]/c.data['open'][i+2,j])]
        for w in (0.,.5,1.):
            values=funded_values(f.vector(i,j,w),forecast)
            for a in range(len(ACTIONS)):
                truth=experience(f,i,j,w,a)
                self.assertAlmostEqual(values[a]['net_return'],truth['net_return'],places=10)
                self.assertAlmostEqual(values[a]['paid_cost'],truth['costs'],places=10)
    def test_market_forecast_inputs_do_not_depend_on_portfolio_exposure(self):
        f=Features(data());self.assertEqual(market_vector(f.vector(260,0,0.)),market_vector(f.vector(260,0,1.)))
        self.assertEqual(len(market_vector(f.vector(260,0,.5))),35)
    def test_future_candles_cannot_change_market_forecast_inputs(self):
        c=data();changed=copy.deepcopy(c)
        for array in changed.data.values():array[261:]=float('nan')
        self.assertEqual(market_vector(Features(c).vector(260,0,0.)),market_vector(Features(changed).vector(260,0,0.)))
    def test_teacher_override_requires_both_development_advantage_and_forecast_skill(self):
        self.assertTrue(gate_overrides([.01,.02],[True,True]))
        self.assertFalse(gate_overrides([.01,-.01],[True,True]));self.assertFalse(gate_overrides([.01,.02],[True,False]));self.assertFalse(gate_overrides([.01],[True]))
        values=[0.]*len(ACTIONS);values[2]=.05
        self.assertEqual(choose(values),1);values[2]=.2;self.assertEqual(choose(values),2)
    def test_teacher_selection_cannot_read_later_scores(self):
        rows={'cash':{'net_return':0.,'later':1000},'trend':{'net_return':.2,'later':-1000}}
        self.assertEqual(best_teacher(rows),'trend');rows['cash']['later']=-1000;rows['trend']['later']=1000;self.assertEqual(best_teacher(rows),'trend')
    def test_distinct_market_labels_are_not_action_or_exposure_duplicates(self):
        raw,market=examples(Features(data()),10**10)
        self.assertEqual(len(raw),len(market)//2*3*len(ACTIONS));self.assertEqual(len({x['id'] for x in market}),len(market));self.assertTrue(all(len(x['vector'])==35 for x in market))
    def test_portfolio_is_funded_once_and_flat_market_loses_exact_costs(self):
        c=data()
        for k in ('open','high','low','close'):c.data[k][:]=100
        with tempfile.TemporaryDirectory() as d:
            ledger=DecisionLedger(Path(d)/'decisions.sqlite3')
            try:
                r=evaluate(Features(c),201*86400,400*86400,ledger,'flat',fixed='buy_hold')
                self.assertEqual(r['starting_equity'],1000.)
                self.assertAlmostEqual(r['ending_equity'],1000*.99/1.01,places=7)
                self.assertAlmostEqual(r['paid_costs'],1000-r['ending_equity'],places=7)
            finally:ledger.close()
    @unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'),'Actual pinned Core required')
    def test_actual_core_full_replay_coverage_and_full_state_recovery(self):
        core=Path(os.environ['BEAN_CORE_DIR']);batch=[{'id':str(i),'decision_at':1,'outcome_at':2,'vector':[i/128.]+[0.]*34,'action':i%2,'reward':float(i%3)} for i in range(128)]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);w=Worker(core,root/'full.sqlite3','factor',10,128)
            try:
                initial=w.call('status')
                with self.assertRaisesRegex(RuntimeError,'Future'):w.call('teach',examples=[{**batch[0],'outcome_at':20}])
                self.assertEqual(initial,w.call('status'))
                w.call('teach',examples=batch);coverage=w.call('train',updates=4)
                self.assertEqual(coverage['discarded_before_training'],0);self.assertLessEqual(coverage['sampled_replay_entries'],128);self.assertGreater(coverage['sampled_replay_entries'],64)
                entry=w.call('checkpoint',coverage=coverage,protocol_sha256='a'*64)
                self.assertNotEqual(initial['weights_sha256'],entry['online_weights_sha256'])
                self.assertTrue(recover(root,core,entry)['full_training_state_matches'])
            finally:w.close()

if __name__=='__main__':unittest.main()
