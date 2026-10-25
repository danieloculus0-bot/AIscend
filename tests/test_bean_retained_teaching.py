import copy,gzip,hashlib,json,os,tempfile,unittest
from pathlib import Path
import numpy as np
from src.bean_multistep_environment import VenueAccount,branch,observation,teaching_rows,withheld
from src.bean_strategy_curriculum import Features,PatternMemory
from src.bean_retained_lessons import match_lesson,load_lessons,active_memory,RetainedMemory,RecallPolicy,digest
from src.bean_teaching_study import train_order
from src.bean_cached_search import learn_cached
from src.bean_wide_multistep import evaluate
from src.bean_causal_simulation import DecisionLedger
from src.bean_wide_history import DAY,canonical,save_gzip
from tests.test_bean_strategy_curriculum import data

META={'base_increment':'.00000001','min_market_funds':'1'}

def case(i,vector,value=None):
    return dict(id=f'lesson{i}',symbol=f'MEM{i}-USD',label_symbol=f'MEM{i}-USD',decision_at=10,outcome_at=20,vector=list(vector),rewards=[0.,float(i-20 if value is None else value)],regime='00',reason='MK',source_ids=[f'cash{i}',f'long{i}'])

class RetainedTeachingContracts(unittest.TestCase):
    def setUp(self):self.f=Features(data())
    def test_matched_advantage_and_why_reconcile_without_altering_inputs(self):
        a=VenueAccount(1000,META);rows=[]
        for action in (0,1):
            _,row=branch(self.f,260,a,action);row.update(product='X-USD',teacher='cash',episode=0,id=str(action));rows.append(row)
        lesson=match_lesson(*rows)
        self.assertEqual(lesson['vector'],rows[0]['vector']);self.assertEqual(lesson['rewards'][1],rows[1]['reward']-rows[0]['reward'])
        self.assertAlmostEqual(sum(lesson['effect_delta'].values()),rows[1]['wealth_after']-rows[0]['wealth_after'])
        self.assertEqual(lesson['outcome_at'],rows[0]['outcome_at']+7*DAY)
        changed=copy.deepcopy(rows[1]);changed['vector'][0]+=1
        with self.assertRaises(ValueError):match_lesson(rows[0],changed)
    def test_flat_price_loss_is_identified_as_cost_not_market(self):
        c=data()
        for k in ('open','close','low','high'):c.data[k][:]=100.
        f=Features(c);rows=[]
        for action in (0,1):
            _,r=branch(f,260,VenueAccount(1000,META),action);r.update(product='X-USD',teacher='cash',episode=0,id=str(action));rows.append(r)
        lesson=match_lesson(*rows);self.assertLess(lesson['rewards'][1],0);self.assertEqual(lesson['reason'],'TC')
    def test_bounded_memory_keeps_negative_cases_and_recovers_exactly(self):
        v=observation(self.f,260,VenueAccount(1000,META));cases=[case(i,v) for i in range(48)]
        # Use actual visible regime calculation, not an arbitrary fixture bucket.
        from src.bean_retained_lessons import regime
        for x in cases:x['regime']=regime(v)
        state=active_memory(cases,30,per_bucket=32)
        self.assertEqual(len(state['entries']),32);self.assertTrue(any(x['rewards'][1]<0 for x in state['entries']));self.assertTrue(any(x['rewards'][1]>0 for x in state['entries']))
        self.assertTrue(all(len(x['code'])==2 for x in state['entries']))
        a=RetainedMemory(state);b=RetainedMemory(json.loads(canonical(state)))
        self.assertEqual(a.query(v,'QUERY-USD',30),b.query(v,'QUERY-USD',30))
        with self.assertRaises(ValueError):a.query(v,'QUERY-USD',29)
    def test_label_shuffled_control_excludes_same_feature_and_label_asset(self):
        from src.bean_retained_lessons import regime
        v=observation(self.f,260,VenueAccount(1000,META));cases=[case(i,v) for i in range(48)]
        for x in cases:x['regime']=regime(v)
        state=active_memory(cases,30);memory=RetainedMemory(state,placebo=True);found=memory.query(v,'MEM10-USD',30)
        self.assertTrue(found['placebo']);self.assertTrue(found['usable'])
        legend={x['code']:x for x in state['entries']}
        for code in found['codes']:self.assertNotEqual(legend[code]['symbol'],'MEM10-USD')
        for code in found['label_codes']:self.assertNotEqual(legend[code]['symbol'],'MEM10-USD')
        self.assertNotEqual(found['codes'],found['label_codes'])
    def test_older_pattern_memory_defaults_preserve_behavior_and_reject_label_alias(self):
        groups=[dict(symbol='A',label_symbol='QUERY',outcome_at=1,vector=[0.],rewards=[0.,9.]),dict(symbol='B',outcome_at=1,vector=[0.],rewards=[0.,2.])]
        r=PatternMemory(groups).query([0.],'QUERY',2)
        self.assertEqual(r['count'],1);self.assertEqual(r['mean_rewards'],[0.,2.])
    def test_retrieval_receipts_are_consumed_by_the_real_portfolio_evaluator(self):
        # Synthetic mechanics test: known past cases, not market-profit evidence.
        from src.bean_retained_lessons import regime
        v=observation(self.f,260,VenueAccount(1000,META));cases=[case(i,v,1.) for i in range(48)]
        for x in cases:x['regime']=regime(v);x['outcome_at']=250*DAY
        memory=RetainedMemory(active_memory(cases,250*DAY));policy=RecallPolicy(None,memory,250*DAY)
        with tempfile.TemporaryDirectory() as d:
            ledger=DecisionLedger(Path(d)/'decisions.sqlite3')
            # Local no-torch environments can still check the actual memory decision.
            reply=policy.predict([v],['QUERY-USD'],261*DAY)
            self.assertGreater(reply['values'][0][1],0);self.assertTrue(reply['contexts'][0]['usable'])
            if os.environ.get('BEAN_CORE_DIR'):
                evaluate({'QUERY-USD':self.f},{'QUERY-USD':META},['QUERY-USD'],261*DAY,270*DAY,'memory_only',ledger,model=policy)
                entries=[json.loads(x[0]) for x in ledger.db.execute('SELECT payload_json FROM decisions')]
                self.assertTrue(any(x.get('retained_lesson_evidence',{}).get('usable') for x in entries))
            ledger.close()

@unittest.skipUnless(os.environ.get('BEAN_CORE_DIR'),'Pinned actual Core executes in GitHub CI')
class ActualCoreTeachingContracts(unittest.TestCase):
    def test_grouped_baseline_is_exact_and_teaching_changes_and_retains_real_weights(self):
        f=Features(data());product=next(f'ASSET{i}-USD' for i in range(100) if not withheld(f'ASSET{i}-USD'))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);corpus=root/'corpus';(corpus/'teaching').mkdir(parents=True);path=corpus/'teaching'/(product+'.jsonl.gz')
            rows=list(teaching_rows(product,f,META,400*DAY,('cash','buy_hold','price_trend','donchian_breakout')))
            with gzip.GzipFile(filename=str(path),mode='wb',mtime=0) as dest:
                for row in rows:dest.write((canonical(row)+'\n').encode())
            (corpus/'artifacts_manifest.json').write_text(json.dumps({'files':{str(path.relative_to(corpus)):{'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}}))
            lessons,source=load_lessons(corpus,450*DAY);reference,stats,_=learn_cached(corpus,Path(os.environ['BEAN_CORE_DIR']),root/'reference',450*DAY,[41],gammas=(0.,))
            self.assertEqual(source['source_sha256'],stats['source_sha256']);out=root/'trained';out.mkdir();results={}
            for mode in ('grouped_raw','shuffled_raw','paired_advantage'):
                model,receipt,diagnostics=train_order(lessons,reference['seed41_gamma0.0'],mode,out,450*DAY)
                self.assertTrue(receipt['full_state_recovered']);self.assertTrue(receipt['weights_changed']);self.assertEqual(receipt['sampled_source_rows'],len(rows));self.assertEqual(receipt['updates'],stats['gradient_updates_per_candidate']);self.assertTrue(diagnostics['curve']);results[mode]=receipt
            self.assertTrue(results['grouped_raw']['baseline_state_exact'])
            self.assertNotEqual(results['grouped_raw']['source_order_sha256'],results['shuffled_raw']['source_order_sha256'])
            self.assertEqual(results['shuffled_raw']['source_order_sha256'],results['paired_advantage']['source_order_sha256'])
            self.assertNotEqual(results['shuffled_raw']['weights_sha256'],results['paired_advantage']['weights_sha256'])

if __name__=='__main__':unittest.main()
