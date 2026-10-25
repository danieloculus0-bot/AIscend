import copy,gzip,hashlib,json,os,tempfile,unittest
from pathlib import Path
import numpy as np
from src.bean_multistep_environment import VenueAccount,observation,withheld,branch,teaching_rows,features_for
from src.bean_strategy_curriculum import Features
from src.bean_wide_multistep import evaluate,train_models
from src.bean_wide_history import DAY
from src.bean_causal_simulation import DecisionLedger
from tests.test_bean_strategy_curriculum import data

META={'base_increment':'.00000001','min_market_funds':'1'}

class MultistepContracts(unittest.TestCase):
    def setUp(self):self.f=Features(data())
    def test_market_and_account_inputs_do_not_consult_future_candles(self):
        altered=copy.deepcopy(self.f.c)
        for a in altered.data.values():a[261:]=np.nan
        a=VenueAccount(1000,META)
        self.assertEqual(observation(self.f,260,a),observation(Features(altered),260,a))
        self.assertEqual(len(observation(self.f,260,a)),39)
    def test_quote_aliases_cannot_cross_asset_split(self):
        for p in ('BTC','ETH','DOGE','SHIB','SOL'):
            self.assertEqual(withheld(p+'-USD'),withheld(p+'-USDC'))
    def test_cash_long_hold_and_exit_are_funded_and_do_not_pay_repeat_entry(self):
        a=VenueAccount(1000,META);a.trade(1,100);fee=a.costs
        self.assertGreater(a.quantity,0);self.assertGreaterEqual(a.cash,0)
        a.trade(1,110);self.assertEqual(a.costs,fee)
        a.trade(0,110);self.assertAlmostEqual(a.quantity,0,places=7)
        self.assertGreater(a.cash,1000);self.assertEqual(a.orders,2)
    def test_order_minimum_unknown_rules_and_rounding_change_actual_orders(self):
        a=VenueAccount(.5,META);a.trade(1,100);self.assertEqual(a.orders,0)
        a=VenueAccount(1000,{});a.trade(1,100);self.assertEqual(a.orders,0)
        a=VenueAccount(1000,dict(base_increment='3',min_market_funds='1'));a.trade(1,100)
        self.assertEqual(a.quantity,9);self.assertGreaterEqual(a.cash,0)
    def test_reward_is_linked_to_later_account_state_and_reconciles_cost_channels(self):
        a=VenueAccount(1000,META)
        following,row=branch(self.f,260,a,1)
        self.assertFalse(row['done']);self.assertEqual(row['next_vector'],observation(self.f,267,following))
        self.assertGreater(row['execution_at'],row['decision_at'])
        self.assertAlmostEqual(sum(row['effects'].values()),row['wealth_after']-row['wealth_before'])
        self.assertEqual(row['vector'][-4],0);self.assertGreater(row['next_vector'][-4],.99)
        _,end=branch(self.f,267,following,1,terminal=True)
        self.assertTrue(end['done']);self.assertIsNone(end['next_vector'])
    def test_training_does_not_cross_maturity_cutoff_and_gaps_are_not_imputed(self):
        cutoff=int(self.f.c.times[300])+DAY
        rows=list(teaching_rows('X-USD',self.f,META,cutoff,['cash','buy_hold']))
        self.assertTrue(rows);self.assertTrue(any(not r['done'] for r in rows))
        self.assertTrue(all(r['outcome_at']<=cutoff for r in rows))
        broken=copy.deepcopy(self.f.c)
        for a in broken.data.values():a[265]=np.nan
        with self.assertRaises(ValueError):branch(Features(broken),260,VenueAccount(1000,META),1)
    def test_funded_portfolio_total_is_1000_and_alternative_reconciles_execution(self):
        start=int(self.f.c.times[260])+DAY;end=int(self.f.c.times[290])+DAY
        with tempfile.TemporaryDirectory() as d:
            ledger=DecisionLedger(Path(d)/'d.sqlite3')
            r=evaluate({'X-USD':self.f,'Y-USD':self.f},{'X-USD':META,'Y-USD':META},['X-USD','Y-USD'],start,end,'buy_hold',ledger)
            rows=[json.loads(x[0]) for x in ledger.db.execute('select payload_json from decisions')]
            self.assertEqual(r['starting_equity'],1000)
            self.assertEqual(sum(x['initial_equity'] for x in r['market_outcomes'].values()),1000)
            self.assertEqual(r['orders'],4)
            self.assertTrue(any('reference_wealth_after' in x for x in rows))
            self.assertTrue(all(x['execution_at']>x['decision_at'] for x in rows if 'execution_at' in x))
            ledger.close()
    def test_missing_terminal_price_cannot_produce_retroactive_liquidation_profit(self):
        changed=copy.deepcopy(self.f.c)
        for array in changed.data.values():array[282:]=np.nan
        f=Features(changed);start=int(f.c.times[260])+DAY;end=int(f.c.times[290])+DAY
        with tempfile.TemporaryDirectory() as d:
            ledger=DecisionLedger(Path(d)/'d.sqlite3')
            r=evaluate({'X-USD':f},{'X-USD':META},['X-USD'],start,end,'buy_hold',ledger)
            self.assertLess(r['ending_equity'],.001);self.assertGreater(r['missing_daily_marks'],0)
            self.assertGreater(r['market_outcomes']['X-USD']['unliquidated_quantity'],0);ledger.close()

@unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'),'Actual Core tested in the pinned GitHub job')
class ActualCoreMultistepContracts(unittest.TestCase):
    def test_real_gradient_coverage_recovery_and_withheld_asset_exclusion(self):
        f=Features(data());cutoff=int(f.c.times[300])+DAY
        known=next(f'ASSET{i}-USD' for i in range(50) if not withheld(f'ASSET{i}-USD'))
        unseen=next(f'ASSET{i}-USD' for i in range(50) if withheld(f'ASSET{i}-USD'))
        with tempfile.TemporaryDirectory() as d:
            models,training,retained=train_models(Path(os.environ['BEAN_CORE_DIR']),{known:f,unseen:f},{known:META,unseen:META},Path(d),cutoff,'protocol-test')
            self.assertFalse((Path(d)/'teaching'/(unseen+'.jsonl.gz')).exists())
            self.assertGreater(training['nonterminal'],0)
            for receipt in retained:
                self.assertTrue(receipt['recovered'])
                if receipt['name']!='untrained':
                    self.assertEqual(receipt['sampled_rows'],training['rows']);self.assertTrue(receipt['weights_changed'])
                else:self.assertEqual(receipt['updates'],0)
            self.assertEqual(models['linked'].gamma,.99);self.assertEqual(models['immediate'].gamma,0)
            self.assertNotEqual(models['linked'].weights_digest(),models['immediate'].weights_digest())
            ledger=DecisionLedger(Path(d)/'eval.sqlite3');before=models['linked'].training_state()
            evaluate({known:f},{known:META},[known],cutoff,int(f.c.times[320])+DAY,'linked',ledger,model=models['linked'])
            self.assertEqual(models['linked'].training_state(),before);ledger.close()

if __name__=='__main__':unittest.main()
