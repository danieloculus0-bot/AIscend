"""Explain profitable experts using matched accounting and teach BEAN their advantage.

The comparison uses exactly the same starting account and realized price path.
Hindsight mentors are training labels only; no outcome component is an inference
feature. This is a development comparison, not a new unseen profitability test.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import numpy as np
from src.bean_strategy_curriculum import ACTIONS,Account,Features,load_candles,evaluate
from src.bean_causal_simulation import DecisionLedger
from src.strategy_atlas import canonical,digest,utc_seconds
from scripts.package_bean_curriculum import RULES

def mentor_lesson(features,i,j,exposure):
    c=features.c;p=c.data['close'][i,j];fill=c.data['open'][i+2,j];end=c.data['close'][i+8,j];cases=[]
    for name,target in zip(ACTIONS,features.targets(i,j,exposure)):
        account=Account(1-exposure,exposure/p);before=account.quantity*(fill-p);account.trade(target,fill);after=account.quantity*(end-fill);net=account.value(end)-1
        if not math.isclose(before+after-account.costs,net,abs_tol=1e-10):raise ValueError('Accounting attribution failed')
        cases.append({'action':name,'rule_mechanism':RULES[name],'target_exposure':target,'market_pnl_before_fill':before,'market_pnl_after_fill':after,'paid_cost':account.costs,'net_return':net,'raw_log_reward':100*math.log(1+net)})
    for case in cases:case['advantage_over_hold']=100*math.log((1+case['net_return'])/(1+cases[1]['net_return']))
    lesson={'schema':'aiscend.bean.matched_mentor_lesson.v1','symbol':c.symbols[j],'decision_at':int(c.times[i])+86400,'outcome_at':int(c.times[i+8])+86400,'prior_exposure':exposure,'vector':features.vector(i,j,exposure),'cases':cases,'interpretation':'Same starting account and price path; accounting explanation of realized outcome. Best hindsight action is a teaching label, never a live decision input.'}
    lesson['id']=digest(lesson);return lesson

def summaries(lessons):
    out=[]
    for x in lessons:
        best=max(x['cases'],key=lambda c:c['net_return']);out.append({'id':x['id'],'outcome_at':x['outcome_at'],'vector':x['vector'],'best_teacher':{'action':best['action'],'mechanism':best['rule_mechanism'],'market_pnl':best['market_pnl_before_fill']+best['market_pnl_after_fill'],'paid_cost':best['paid_cost'],'net_return':best['net_return'],'advantage_over_hold':best['advantage_over_hold']}})
    return out

def teach_and_train_streaming(worker, lessons, order, *, updates=1024, chunk_size=32):
    """Train while teacher cases still exist in the bounded BEAN replay.

    SpongeDQN in bean_mentor_worker uses a 4,096-transition replay and has
    auto-training disabled (learn_every=1,000,000). Teaching every lesson
    before the first gradient silently evicts most earlier examples.
    This schedule gives every newly ingested chunk an opportunity to be
    sampled before it is evicted. It does NOT guarantee every item is drawn
    or any financial improvement. Both experimental arms use the same
    ordering, cumulative budget, and schedule.
    """
    n = len(lessons)
    indices = [int(index) for index in order]
    actions = len(ACTIONS)
    if not n or len(indices) != n or set(indices) != set(range(n)):
        raise ValueError('Mentor training requires each lesson exactly once')
    if n * actions < 64:
        raise ValueError('Not enough mentor replay samples for BEAN warmup')
    if not 1 <= chunk_size or chunk_size * actions > 4096:
        raise ValueError('Mentor batch must fit the 4,096-frame BEAN replay')
    if not isinstance(updates, int) or not 1 <= updates <= 100000:
        raise ValueError('Invalid total mentor gradient budget')
    starting = worker.call('status')
    if (starting['frames'] != 0 or starting['updates'] != 0
            or starting['mentor_lessons'] != 0):
        raise ValueError('Streamed mentor schedule requires a fresh learner')
    learned = 0
    training_steps = 0
    consumed = 0
    for offset in range(0, n, chunk_size):
        batch = indices[offset:offset + chunk_size]
        receipt = worker.call('teach', lessons=[lessons[index] for index in batch])
        consumed += len(batch)
        frames = consumed * actions
        if receipt['frames'] != frames or receipt['lessons'] != consumed:
            raise AssertionError('Mentor worker lost or duplicated teaching evidence')
        target = (consumed * updates) // n
        if frames >= 64 and target > learned:
            result = worker.call('train', updates=target)
            if result['updates'] != target:
                raise AssertionError('Mentor gradient budget did not advance')
            learned = target
            training_steps += 1
    if learned != updates:
        raise AssertionError('Mentor training ended before full gradient budget')
    return {'lessons': consumed, 'frames': consumed * actions,
            'updates': learned, 'training_steps': training_steps,
            'training_schedule': 'streamed_cumulative_budget_v1'}


class CoachWorker:
    def __init__(self,core,journal,size,cutoff,mode,memory,output):
        env={k:v for k,v in os.environ.items() if k in ('PATH','PYTHONPATH','LD_LIBRARY_PATH','LANG')}
        self.p=subprocess.Popen([sys.executable,'-m','src.bean_mentor_worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,env=env)
        self.memory=memory;self.matrix=np.array([x['vector'] for x in memory]);self.receipts=(Path(output)/(mode+'_mentor_receipts.jsonl')).open('w');self.explanations=(Path(output)/(mode+'_decision_explanations.jsonl')).open('w')
        self.call('init',core_dir=str(Path(core).resolve()),journal=str(journal),seed=41,size=size,actions=list(ACTIONS),knowledge_cutoff=cutoff,mode=mode)
    def call(self,op,**kw):
        if op=='values':
            distances=np.mean((self.matrix-np.array(kw['vectors'][0]))**2,axis=1);ix=np.argsort(distances,kind='stable')[:4];kw['mentor_evidence']=[{k:v for k,v in self.memory[n].items() if k!='vector'} for n in ix]
        self.p.stdin.write(canonical({'op':op,**kw})+'\n');self.p.stdin.flush();line=self.p.stdout.readline()
        if not line:raise RuntimeError(self.p.stderr.read()[-3000:])
        r=json.loads(line)
        if not r['ok']:raise RuntimeError(r['error'])
        result=r['result']
        if op=='teach':self.receipts.write(canonical(result)+'\n');self.receipts.flush()
        if op=='values':self.explanations.write(canonical({'decision_at':kw['decision_at'],'vector_sha256':digest(kw['vectors'][0]),'response':result})+'\n');self.explanations.flush()
        return result
    def close(self):
        if self.p.poll() is None:self.call('close');self.p.wait(timeout=15)
        for p in (self.p.stdin,self.p.stdout,self.p.stderr,self.receipts,self.explanations):p.close()

def run(candles,core,output):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve prior mentor experiment')
    output.mkdir(parents=True);features=Features(load_candles(candles));cutoff=int(utc_seconds('2025-04-01T00:00:00Z'));end=int(utc_seconds('2025-10-01T00:00:00Z'))
    protocol={'schema':'aiscend.bean.strategy_coaching.v2','scope':'DEVELOPMENT_ONLY_ALREADY_INSPECTED_HISTORY','source_rows_sha256':features.c.source_hash,'train_knowledge_cutoff':cutoff,'audit_end':end,'arms':['matched_raw_reward','coached_advantage'],'seed':41,'updates_per_arm':1024,'training_schedule':'streamed_cumulative_budget_v1: 32 lessons/batch, train after each batch with proportional cumulative gradient budget so earlier lessons remain in replay','shared_configuration':'Actual SpongeDQN gamma0, replay4096, batch64, lr0.0005; same examples/order/costs and inference reserve. The only reward difference is raw net log return versus log advantage over unchanged holding.','coaching':'Every action is explained by market PnL before/after delayed fill, paid costs, net return and matched advantage over hold. Worker validates accounting and maturity before actual updates. Past mentor comparisons accompany inference explanations; realized outcome components are never future decision inputs.','selection':'No candidate selection: report both arms even if coached training loses','source_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'worker_sha256':hashlib.sha256(Path(__file__).with_name('bean_mentor_worker.py').read_bytes()).hexdigest(),'core_revision':subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip(),'live_orders':False}
    protocol['protocol_sha256']=digest(protocol);(output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    con=sqlite3.connect(output/'matched_mentor_lessons.sqlite3');con.execute('CREATE TABLE lessons(id TEXT PRIMARY KEY,symbol TEXT,decision_at INTEGER,outcome_at INTEGER,payload_json TEXT)');lessons=[]
    for i in range(200,len(features.c.times)-8,8):
        if int(features.c.times[i+8])+86400>cutoff:continue
        for j in range(len(features.c.symbols)):
            if not features.valid(i,j):continue
            for exposure in (0.,.5,1.):
                x=mentor_lesson(features,i,j,exposure);con.execute('INSERT INTO lessons VALUES (?,?,?,?,?)',(x['id'],x['symbol'],x['decision_at'],x['outcome_at'],canonical(x)));lessons.append(x)
    con.commit();assert con.execute('pragma integrity_check').fetchone()[0]=='ok';con.close()
    memory=summaries(lessons);order=np.random.default_rng(41).permutation(len(lessons));results={};policies=[];ledger=DecisionLedger(output/'decisions.sqlite3');ledger.metadata('protocol',protocol)
    for mode in protocol['arms']:
        journal=output/(mode+'.sqlite3');worker=CoachWorker(core,journal,len(lessons[0]['vector']),cutoff,mode,memory,output)
        try:
            initial=worker.call('status')
            schedule=teach_and_train_streaming(worker,lessons,order,updates=1024,chunk_size=32)
            status=worker.call('status')
            result=evaluate(features,worker,cutoff,end,ledger,mode)
            worker.call('checkpoint',score=result['equal_weight_mean_return'],samples=result['periods']*len(features.c.symbols),evaluation_sha256=digest(result),protocol_sha256=protocol['protocol_sha256'])
            policies.append({'id':mode,'file':mode+'.sqlite3.gz','knowledge_cutoff':'2025-04-01T00:00:00Z','state':status,'economic_qualification':'DEVELOPMENT_ONLY','actual_weights_changed':status['weights_sha256']!=initial['weights_sha256']})
            result['learning_schedule']=schedule
            results[mode]=result
        finally:worker.close()
    baselines={a:evaluate(features,None,cutoff,end,ledger,'mentor_baseline/'+a,fixed=a) for a in ('cash','buy_hold','sma_trend','donchian_breakout')}
    report={'schema':'aiscend.bean.strategy_coach_results.v1','protocol_sha256':protocol['protocol_sha256'],'mentor_lessons':len(lessons),'strategy_comparisons':len(lessons)*len(ACTIONS),'results':results,'baselines':baselines,'coaching_effect_on_mean_return':results['coached_advantage']['equal_weight_mean_return']-results['matched_raw_reward']['equal_weight_mean_return'],'independent_unseen_profitability_proven':False,'general_causal_understanding_proven':False,'live_orders':False,'decision_records':ledger.seq,'decision_ledger_tip':ledger.tip};ledger.metadata('results',report);ledger.close();(output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    for p in list(output.iterdir()):
        if p.suffix in ('.sqlite3','.jsonl'):
            with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    for x in policies:x['file_sha256']=hashlib.sha256((output/x['file']).read_bytes()).hexdigest()
    (output/'retention_manifest.json').write_text(json.dumps({'schema':'aiscend.bean.curriculum_retention.v1','policies':policies},indent=2)+'\n')
    print(canonical({'event':'mentor_training_complete','lessons':len(lessons),'returns':{k:v['equal_weight_mean_return'] for k,v in results.items()},'coaching_effect':report['coaching_effect_on_mean_return']}),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candles',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.candles,a.core_dir,a.output)
