import math
import unittest
from src.bean_route_values import liquidation_values,LiquidationAdapter
from src.bean_economic_values import funded_values,choose
from src.bean_strategy_curriculum import ACTIONS,Account
from scripts.bean_exit_cost_audit import ForecastCache
from src.bean_strategy_curriculum import Features
from tests.test_bean_strategy_curriculum import data
from src.bean_action_aliases import consistent_values

def observation(exposure):
    v=[0.]*47;v[15]=exposure;v[36:]=[0.,exposure,1.]+[0.]*(len(ACTIONS)-3);return v

class ExitCostValueTests(unittest.TestCase):
    def test_bad_raw_estimates_cannot_make_holding_cash_differ_from_cash(self):
        v=observation(0.);q=[10.,-10.]+[3.]*(len(ACTIONS)-2);r=consistent_values(v,q)
        self.assertEqual(r[0],0.);self.assertEqual(r[1],0.)
    def test_equal_targets_get_one_value_but_intermediate_holding_is_distinct(self):
        v=observation(.5);v[36:]=[.5]*len(ACTIONS);q=[float(i) for i in range(len(ACTIONS))];q[1]=100.
        r=consistent_values(v,q)
        self.assertEqual(len({x for i,x in enumerate(r) if i!=1}),1)
        self.assertEqual(r[1],100.)
    def test_adapter_uses_model_forecasts_and_replaces_entry_only_values(self):
        class MarketWorker:
            def call(self,op,**kw):
                if op=='status':return {'updates':123,'weights_sha256':'test'}
                return {'values':[[99.]*len(ACTIONS)],'market_forecasts':[[0.,1.5]]}
        adapter=LiquidationAdapter(MarketWorker());r=adapter.call('values',vectors=[observation(0.)],decision_at=1)
        self.assertEqual(choose(r['values'][0]),1);self.assertGreater(r['valuation_components'][0][2]['pending_exit_cost'],0)
        self.assertEqual(adapter.call('status')['updates'],123)
    def test_forecast_cache_refuses_realized_outcome_labels(self):
        row={'symbol':'BTCUSDT','decision_at':261*86400,'forecast':[0.,0.],'outcome':[99.,99.]}
        with self.assertRaisesRegex(ValueError,'Only saved predictions'):ForecastCache(Features(data()),[row],0,'gross_mark','test')
    def test_round_trip_can_erase_an_apparent_entry_only_gain(self):
        v=observation(0.);forecast=[0.,1.5]
        old=[x['net_log_return'] for x in funded_values(v,forecast)];new=[x['net_log_return'] for x in liquidation_values(v,forecast)]
        self.assertGreater(old[2],0);self.assertLess(new[2],0)
        self.assertEqual(choose(old),2);self.assertEqual(choose(new),1)
    def test_holding_does_not_charge_a_new_exit_fee_every_period(self):
        for exposure in (0.,.5,1.):
            rows=liquidation_values(observation(exposure),[0.,0.])
            self.assertAlmostEqual(rows[1]['net_liquidation_return'],0.)
            self.assertEqual(rows[1]['paid_cost'],0.)
    def test_cash_and_holding_cash_remain_identical(self):
        rows=liquidation_values(observation(0.),[3.,-10.])
        self.assertEqual(rows[0]['net_log_return'],rows[1]['net_log_return']);self.assertEqual(rows[0]['net_log_return'],0.)
    def test_net_liquidation_returns_telescope_to_actual_final_cash(self):
        a=Account(1000.);price=100.;product=1.
        for action,gap,after in ((2,1.02,1.10),(1,.98,.95),(0,1.01,1.04)):
            w=a.exposure(price);v=observation(w);forecast=[100*math.log(gap),100*math.log(after)]
            row=liquidation_values(v,forecast)[action];product*=1+row['net_liquidation_return']
            a.trade(None if action==1 else v[36+action],price*gap);price*=gap*after
        self.assertAlmostEqual(a.quantity,0.);self.assertAlmostEqual(a.cash/1000.,product,places=10)

if __name__=='__main__':unittest.main()
