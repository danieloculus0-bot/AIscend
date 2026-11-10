"""Synthetic-only contracts; a fake core is NOT evidence of BEAN training."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research import bean_rotation_shadow_study as m

class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=m.synthetic_fixture(count=320)
        cls.market=m.load_bars(cls.raw)

    def test_strict_source_and_canonical_sha(self):
        self.assertEqual(m.load_bars(self.raw)['source_sha256'],m.sha(self.raw))
        for key,bad in [('open_time',m.BAR+.5),('open_time',True),('volume',0),('close',float('inf'))]:
            rows=copy.deepcopy(self.raw)
            rows[2][key]=bad
            with self.assertRaises(ValueError,msg=key):m.load_bars(rows)
        rows=copy.deepcopy(self.raw)
        rows.pop()
        with self.assertRaisesRegex(ValueError,'UNALIGNED_ASSETS'):m.load_bars(rows)

    def test_native_coinbase_ohcv_with_ts_and_no_high_low(self):
        data=[{k:v for k,v in r.items() if k in ('symbol','open','close','volume')} | {'ts':r['open_time']} for r in self.raw]
        aligned=m.load_bars(data)
        self.assertEqual(aligned['symbols'],self.market['symbols'])
        self.assertEqual(m.observation(aligned,80,None),m.observation(self.market,80,None))
        self.assertIsNone(aligned['bars'][0][0][1])
        self.assertIsNone(aligned['bars'][0][0][2])

    def test_no_future_candle_leakage(self):
        base=m.load_bars(self.raw)
        i=80
        before=(m.observation(base,i,None),m.targets(base,i))
        rows=copy.deepcopy(self.raw)
        for r in rows:
            if r['open_time']>=m.BAR*(i+1):
                r['open']*=5; r['close']*=5; r['high']*=5; r['low']*=5
        after=m.load_bars(rows)
        self.assertEqual(before,(m.observation(after,i,None),m.targets(after,i)))

    def test_delayed_entry_cost_and_hold(self):
        market=self.market;i=40
        pos,cash,qty,event=m.step(market,i,None,1000.,0.,1)
        self.assertEqual(event['behavior'],'REENTER')
        self.assertEqual(event['entry_ts'],market['times'][i+2])
        self.assertEqual(event['signal_ts'],market['times'][i]+m.BAR)
        self.assertEqual(event['matured_ts'],market['times'][i+8]+m.BAR)
        self.assertAlmostEqual(qty,1000*(1-m.COST)/market['bars'][pos][i+2][0],places=10)
        next_action=m.targets(market,i+8).index(pos)
        p2,c2,q2,hold=m.step(market,i+8,pos,cash,qty,next_action)
        self.assertEqual(hold['behavior'],'HOLD')
        self.assertEqual(hold['fills'],[])
        self.assertEqual((p2,c2,q2),(pos,cash,qty))
        p3,c3,q3,sell=m.step(market,i+16,pos,cash,qty,0)
        self.assertEqual(sell['behavior'],'DEFEND')
        self.assertIsNone(p3)
        self.assertEqual(q3,0.)
        self.assertAlmostEqual(c3,qty*market['bars'][pos][i+18][0]*(1-m.COST),places=8)

    def test_distinct_targets_and_rotation(self):
        market=self.market;i=56
        leader=m.targets(market,i)[1]
        self.assertEqual(len(set(m.targets(market,i))),3)
        pos,cash,qty,_=m.step(market,i,None,1000.,0.,1)
        runner=m.targets(market,i+8)[2]
        action=m.targets(market,i+8).index(runner)
        pos2,cash2,qty2,event=m.step(market,i+8,pos,cash,qty,action)
        self.assertEqual(event['behavior'],'ROTATE')
        self.assertEqual(pos2,runner)
        self.assertEqual(len(event['fills']),2)
        self.assertGreater(qty2,0)

    def test_atomic_capacity_failure_keeps_existing_position(self):
        rows=copy.deepcopy(self.raw)
        market=m.load_bars(rows);i=64
        pos,cash,qty,_=m.step(market,i,None,1000.,0.,1)
        # Make sale impossible while retaining a fully valid positive-volume bar.
        fill_index=i+8+2
        for r in rows:
            if r['symbol']==market['symbols'][pos] and r['open_time']==market['times'][fill_index]:r['volume']=1e-4
        constrained=m.load_bars(rows)
        p2,c2,q2,event=m.step(constrained,i+8,pos,cash,qty,0)
        self.assertEqual(event['behavior'],'UNFILLED')
        self.assertEqual(event['unfilled_reason'],'SELL_CAPACITY')
        self.assertEqual(event['fills'],[])
        self.assertEqual((p2,c2,q2),(pos,cash,qty))
        self.assertTrue(math.isfinite(event['reward']))

    def test_cash_is_cost_free_and_remains_abstention(self):
        r=m.rollout(self.market,40,200,lambda s,p,i:0)
        self.assertEqual(r['net_return'],0.)
        self.assertEqual(r['unfilled'],0)
        self.assertTrue(all(e['behavior']=='ABSTAIN' for e in r['events']))


    def test_long_horizon_features_causal_and_present(self):
        rows=m.synthetic_fixture(count=1720)
        market=m.load_bars(rows)
        early=m.observation(market,700,None)
        self.assertEqual(len(early),15)
        self.assertEqual(early[-2:],[0.,0.])
        at=m.observation(market,1500,None)
        self.assertEqual(at[-2:],[1.,1.])
        import copy
        modified=copy.deepcopy(rows)
        for row in modified:
            if row['open_time']>1500*m.BAR:
                row['open']*=3;row['high']*=3;row['low']*=3;row['close']*=3
        future=m.load_bars(modified)
        self.assertEqual(at,m.observation(future,1500,None))

    def test_human_defensive_btc_trend_control(self):
        rows=m.synthetic_fixture(count=1900)
        for r in rows:
            if r['symbol']=='ALPHA-USD':
                r['symbol']='BTC-USD'
            else:
                r['symbol']='ETH-USD'
            if r['symbol']=='BTC-USD':
                scale=math.exp(-.001*(r['open_time']//m.BAR))
                r['open']=100.*scale
                r['close']=r['open']*.999
                r['high']=r['open']*1.01
                r['low']=r['close']*.99
        market=m.load_bars(rows)
        state=m.observation(market,1650,None)
        self.assertEqual(len(state),15)
        self.assertEqual(m.fixed(market,'btc_200d_12m_defensive')(state,None,1650),0)
        # A years-long recovery must reopen BTC exposure.
        for r in rows:
            if r['symbol']=='BTC-USD':
                scale=math.exp(.001*(r['open_time']//m.BAR))
                r['open']=100.*scale
                r['close']=r['open']*1.001
                r['high']=r['close']*1.01
                r['low']=r['open']*.99
        market=m.load_bars(rows)
        action=m.fixed(market,'btc_200d_12m_defensive')(state,None,1650)
        self.assertEqual(m.targets(market,1650)[action],market['symbols'].index('BTC-USD'))


    def test_mentor_rejected_for_unqualified_synthetic_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,"MENTOR_REQUIRES_EXACT_PUBLIC"):
                m.train("no-core",self.market,Path(tmp)/"not_created",
                        training_policy="mentor")
            self.assertFalse((Path(tmp)/"not_created").exists())

    def test_teacher_decisions_train_real_interface_without_future_labels(self):
        # TEST DOUBLE ONLY. Actual BEAN Core is separately checked in GitHub CI.
        class DummyCore:
            def __init__(self,seed):
                self.seed=seed;self.frames=0;self.updates=0
            def weights_digest(self):
                return ("0" if not self.updates else "a")*64
            def choose(self,obs,epsilon=0):
                self.assert_dimension(obs)
                return 0
            @staticmethod
            def assert_dimension(obs):
                assert len(obs)==15
            def observe(self,obs,a,reward,next_obs,done):
                assert len(obs)==15 and len(next_obs)==15 and math.isfinite(reward)
                self.frames+=1;self.updates+=1
            def training_state(self):
                return {"schema":"DUMMY_NOT_BEAN","frames":self.frames,"updates":self.updates}
            @classmethod
            def from_training_state(cls,state):
                x=cls(0);x.frames=state["frames"];x.updates=state["updates"];return x
        rows=m.synthetic_fixture(count=2400)
        for r in rows:r["symbol"]={"ALPHA-USD":"BTC-USD","BETA-USD":"ETH-USD"}[r["symbol"]]
        market=m.load_bars(rows)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(m,"core_learner",side_effect=lambda root,seed:DummyCore(seed)):
                teacher=m.train("not-actual-core",market,Path(tmp)/"mentor",seeds=(7,),
                                epochs=1,source_kind="public_historical",training_policy="mentor")
                placebo=m.train("not-actual-core",market,Path(tmp)/"placebo",seeds=(7,),
                                epochs=1,source_kind="public_historical",training_policy="shuffled_mentor")
            import gzip
            def events(name):
                with gzip.open(Path(tmp)/name/"seed-7.training_events.jsonl.gz","rt") as z:
                    return [json.loads(line) for line in z]
            original=events("mentor")
            shuffled=events("placebo")
            self.assertEqual(len(original),len(shuffled))
            self.assertEqual(len(original),teacher["learned"][0]["training_frames"])
            self.assertEqual(teacher["learned"][0]["gradient_updates"],
                             placebo["learned"][0]["gradient_updates"])
            self.assertGreater(sum(a["requested_target"]!=b["requested_target"]
                                   for a,b in zip(original,shuffled)),0)
            for e in original:
                i=(e["signal_ts"]-m.BAR)//m.BAR
                self.assertEqual(e["action"],m.ACTIONS[
                    m.fixed(market,"btc_200d_12m_defensive")(None,None,i)])
                self.assertLess(e["signal_ts"],e["entry_ts"])
                self.assertLess(e["entry_ts"],e["matured_ts"])
            self.assertTrue(teacher["learned"][0]["checkpoint_replay_verified"])
            self.assertFalse(teacher["promotion_authorized"])

    def test_wrong_core_checkout_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'SEPARATE_BEAN_GIT_CHECKOUT_REQUIRED'):
                m.core_learner(d,7)

    def test_paper_training_protocol_never_promotes(self):
        class DummyCore:
            def __init__(self,seed):self.seed=seed;self.frames=0;self.updates=0
            def weights_digest(self):return ('0' if self.updates==0 else 'a')*64
            def choose(self,obs,epsilon=0):
                assert len(obs)==15
                return 1
            def observe(self,obs,a,r,next_obs,done):
                assert math.isfinite(r)
                self.frames+=1;self.updates+=1
            def training_state(self):return {'schema':'DUMMY_ONLY_NOT_BEAN','frames':self.frames,'updates':self.updates}
            @classmethod
            def from_training_state(cls,state):
                x=cls(0);x.frames=state['frames'];x.updates=state['updates'];return x
        market=m.load_bars(m.synthetic_fixture(count=1200))
        created=[]
        def factory(_dir,seed):
            x=DummyCore(seed);created.append(x);return x
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'new'
            with patch.object(m,'core_learner',side_effect=factory):
                result=m.train('unavailable-core',market,out,seeds=(7,41),epochs=2)
            self.assertFalse(result['promotion_authorized'])
            self.assertFalse(result['live_orders'])
            self.assertEqual(result['result_status'],'EXPERIMENTAL_SYNTHETIC_NO_PROFITABILITY_CLAIM')
            self.assertEqual(result['protocol']['training_last_outcome_index'],result['protocol']['first_eval_index'])
            self.assertEqual(len(created),4)
            self.assertTrue(all(x.frames>0 for x in (created[0],created[2])))
            self.assertEqual(created[0].frames,created[2].frames)
            self.assertEqual(result['controls']['cash']['net_return'],0.)
            for x in result['learned']:
                self.assertEqual(x['training_frames'],created[0].frames)
                self.assertTrue(x['checkpoint_replay_verified'])
                self.assertIn('untrained_evaluation',x)
                self.assertEqual(x['gradient_updates'],x['training_frames'])
                self.assertTrue((out/f"seed-{x['seed']}.training_state.json.gz").exists())
            self.assertEqual(json.loads((out/'results.json').read_text())['result_status'],result['result_status'])
            with patch.object(m,'core_learner',side_effect=factory):
                with self.assertRaises(FileExistsError):m.train('unavailable-core',market,out)

if __name__=='__main__':unittest.main()
