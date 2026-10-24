"""Teach transferable OHLCV strategy outcomes with bounded, retained BEAN training.

All of this study is development. Earlier research inspected overlapping history.
The prior opened final year is physically excluded. Repetition improves fitting,
not the amount of independent market evidence.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
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

import numpy as np
import pandas as pd
from .strategy_atlas import canonical,digest,utc_seconds,iso_time
from .bean_causal_simulation import DecisionLedger
from .bean_training_batches import digest_chunks

DAY=86400;STEP=8;COST=.01
ACTIONS=('cash','hold','buy_hold','sma_trend','price_trend','donchian_breakout','rsi_pullback','bollinger_revert','bullish_engulf','hammer_rebound','risk_scaled_trend')
BUDGETS=(256,1024,4096);SEEDS=(7,41)

@dataclass
class Candles:
    symbols:tuple
    times:np.ndarray
    data:dict
    source_hash:str

def load_candles(path):
    raw=gzip.decompress(Path(path).read_bytes());rows=[json.loads(x) for x in raw.splitlines()]
    if any(r['open_time']>=utc_seconds('2025-10-01T00:00:00Z') for r in rows):raise ValueError('Opened final-period boundary cannot enter this development study')
    symbols=tuple(sorted({r['symbol'] for r in rows}));times=np.array(sorted({r['open_time'] for r in rows}),dtype=np.int64)
    data={k:np.full((len(times),len(symbols)),np.nan) for k in ('open','high','low','close','volume')}
    ti={t:i for i,t in enumerate(times)};si={s:i for i,s in enumerate(symbols)}
    for r in rows:
        for k in data:data[k][ti[r['open_time']],si[r['symbol']]]=r[k]
    return Candles(symbols,times,data,hashlib.sha256(raw).hexdigest())

class Features:
    """No symbol/date identifier, future fitting, or absolute price level in vectors."""
    def __init__(self,candles):
        self.c=candles;p=pd.DataFrame(candles.data['close']);h=pd.DataFrame(candles.data['high']);l=pd.DataFrame(candles.data['low']);v=pd.DataFrame(candles.data['volume'])
        self.sma={n:p.rolling(n,min_periods=n).mean().to_numpy() for n in (20,50,200)}
        self.std=p.rolling(20,min_periods=20).std(ddof=0).to_numpy();self.vol=p.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=0).to_numpy()
        delta=p.diff();up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean();down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
        self.rsi=(100-100/(1+up/down.replace(0,np.nan))).mask((down==0)&(up>0),100).mask((down==0)&(up==0),50).to_numpy()
        self.hi20=h.shift(1).rolling(20,min_periods=20).max().to_numpy();self.lo10=l.shift(1).rolling(10,min_periods=10).min().to_numpy()
        self.vmean=v.shift(1).rolling(20,min_periods=20).mean().to_numpy()
    def vector(self,i,j,exposure):
        d=self.c.data;p=d['close'][i,j];row=[]
        for n,s in ((1,.03),(3,.06),(7,.10),(14,.15),(30,.25),(90,.50)):row.append((p/d['close'][i-n,j]-1)/s)
        row += [(p/self.sma[n][i,j]-1)/.10 for n in (20,50,200)]
        row += [self.vol[i,j]/.03,(self.rsi[i,j]-50)/50,(p-self.sma[20][i,j])/max(self.std[i,j],1e-10)/3,math.log(max(d['volume'][i,j],1)/max(self.vmean[i,j],1)),(p/self.hi20[i,j]-1)/.10,(p/self.lo10[i,j]-1)/.10,exposure]
        for k in range(5):
            o,h,l,c=[d[n][i-k,j] for n in ('open','high','low','close')];span=max(h-l,1e-10)
            row += [(c-o)/span,(h-max(o,c))/span,(min(o,c)-l)/span,(h/l-1)/.10]
        # Explicit rules are teaching features. Their returns are never inputs.
        row += [float(x if x is not None else exposure) for x in self.targets(i,j,exposure)]
        return [float(np.clip(x,-5,5)) for x in row]
    def targets(self,i,j,exposure):
        d=self.c.data;o,h,l,c=[d[n][i,j] for n in ('open','high','low','close')];po,pc=d['open'][i-1,j],d['close'][i-1,j]
        z=(c-self.sma[20][i,j])/max(self.std[i,j],1e-10);rsi=self.rsi[i,j];trend=c>self.sma[200][i,j]
        engulf=pc<po and c>o and o<=pc and c>=po and c>self.sma[50][i,j]
        body=abs(c-o);span=h-l
        hammer=c/d['close'][i-7,j]<1 and min(o,c)-l>=2*max(body,1e-10) and h-max(o,c)<.25*span and c>o
        return (0.,None,1.,float(self.sma[50][i,j]>self.sma[200][i,j]),float(trend),1. if c>self.hi20[i,j] else (exposure if c>=self.lo10[i,j] else 0.),1. if trend and rsi<35 else (exposure if trend and rsi<60 else 0.),1. if z < -2 else (exposure if z<0 else 0.),float(engulf),float(hammer),min(1.,.015/max(self.vol[i,j],.005)) if trend else 0.)
    def valid(self,i,j):
        return i>=200 and all(np.isfinite(self.c.data[k][i-200:i+1,j]).all() for k in self.c.data)

class Account:
    def __init__(self,cash=1.,quantity=0.):self.cash=cash;self.quantity=quantity;self.costs=0.;self.orders=0
    def value(self,price):return self.cash+self.quantity*price
    def exposure(self,price):return min(1.,max(0.,self.quantity*price/max(self.value(price),1e-12)))
    def trade(self,target,price,cost=COST):
        if target is None:return
        if not 0<=target<=1:raise ValueError('Target outside funded exposure')
        equity=self.value(price);difference=target*equity-self.quantity*price
        if abs(difference)<1e-9:return
        amount=min(difference,self.cash/(1+cost)) if difference>0 else difference
        fee=abs(amount)*cost;self.quantity+=amount/price;self.cash-=amount+fee;self.costs+=fee;self.orders+=1
        if self.cash < -1e-8 or self.quantity < -1e-8:raise AssertionError('Unfunded position')
        self.cash=max(0.,self.cash);self.quantity=max(0.,self.quantity)

def experience(features,i,j,exposure,action):
    c=features.c;p=c.data['close'][i,j];account=Account(1-exposure,exposure/p)
    vector=features.vector(i,j,exposure);target=features.targets(i,j,exposure)[action]
    # Delayed next-next opening fill, seven daily bars to the matured outcome.
    account.trade(target,c.data['open'][i+2,j]);end=account.value(c.data['close'][i+STEP,j])
    return {'symbol':c.symbols[j],'decision_index':i,'decision_at':int(c.times[i])+DAY,'outcome_at':int(c.times[i+STEP])+DAY,'vector':vector,'action':action,'target':target,'reward':100*math.log(max(end,1e-12)),'net_return':end-1,'costs':account.costs,'prior_exposure':exposure}

def build_memory(features,database,cutoff):
    path=Path(database)
    if path.exists():raise FileExistsError('Refuse to replace teaching memory')
    con=sqlite3.connect(path);con.execute('CREATE TABLE experiences(id INTEGER PRIMARY KEY,symbol TEXT,decision_at INTEGER,outcome_at INTEGER,action TEXT,reward REAL,prior_exposure REAL,features_json TEXT,evidence_json TEXT)')
    groups=[];all_examples=[]
    for i in range(200,len(features.c.times)-STEP,STEP):
        if int(features.c.times[i+STEP])+DAY>cutoff:continue
        for j in range(len(features.c.symbols)):
            if not features.valid(i,j):continue
            for exposure in (0.,.5,1.):
                examples=[experience(features,i,j,exposure,a) for a in range(len(ACTIONS))]
                groups.append({'symbol':features.c.symbols[j],'decision_at':examples[0]['decision_at'],'outcome_at':examples[0]['outcome_at'],'vector':examples[0]['vector'],'rewards':[x['reward'] for x in examples]})
                for x in examples:
                    con.execute('INSERT INTO experiences(symbol,decision_at,outcome_at,action,reward,prior_exposure,features_json,evidence_json) VALUES (?,?,?,?,?,?,?,?)',(x['symbol'],x['decision_at'],x['outcome_at'],ACTIONS[x['action']],x['reward'],exposure,canonical(x['vector']),canonical({k:v for k,v in x.items() if k!='vector'})))
                    all_examples.append(x)
    con.commit()
    if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Memory integrity failed')
    con.close();return all_examples,groups

class CandleWorker:
    def __init__(self,core,journal,size,seed,cutoff,restore=False):
        env={k:v for k,v in os.environ.items() if k in ('PATH','PYTHONPATH','LD_LIBRARY_PATH','LANG')}
        self.p=subprocess.Popen([sys.executable,'-m','src.bean_curriculum_worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,env=env)
        self.call('init',core_dir=str(Path(core).resolve()),journal=str(journal),seed=seed,size=size,actions=list(ACTIONS),knowledge_cutoff=cutoff,restore=restore)
    def call(self,op,**kw):
        self.p.stdin.write(canonical({'op':op,**kw})+'\n');self.p.stdin.flush();line=self.p.stdout.readline()
        if not line:raise RuntimeError(self.p.stderr.read()[-3000:])
        result=json.loads(line)
        if not result['ok']:raise RuntimeError(result['error'])
        return result['result']
    def close(self):
        if self.p.poll() is None:self.call('close');self.p.wait(timeout=15)
        for x in (self.p.stdin,self.p.stdout,self.p.stderr):x.close()

class PatternMemory:
    def __init__(self,groups):
        self.groups=groups
        self.vectors=np.array([x['vector'] for x in groups]);self.rewards=np.array([x['rewards'] for x in groups])
        self.symbols=np.array([x['symbol'] for x in groups]);self.outcomes=np.array([x['outcome_at'] for x in groups])
    def query(self,vector,symbol,decision_at,k=32):
        # Only mature examples on other instruments. This avoids retrieving the
        # current instrument's exact past trajectory as its own transfer proof.
        known=np.flatnonzero((self.symbols!=symbol)&(self.outcomes<=decision_at))
        if not len(known):return {'count':0,'mean_rewards':[0.]*len(ACTIONS),'max_outcome_at':None}
        distance=np.mean((self.vectors[known]-np.array(vector))**2,axis=1)
        local=np.argsort(distance,kind='stable')[:k];chosen=known[local]
        return {'count':len(chosen),'mean_rewards':self.rewards[chosen].mean(axis=0).tolist(),'max_outcome_at':int(self.outcomes[chosen].max()),'mean_squared_distance':float(distance[local].mean()),'memory_group_ids':chosen.tolist()}

def evaluate(features,worker,start,end,ledger,label,*,fixed=None,memory=None):
    c=features.c;valid_indices=[i for i in range(200,len(c.times)-STEP,STEP) if start<=int(c.times[i])+DAY and int(c.times[i+STEP])+DAY<=end]
    portfolios={s:Account(1000.) for s in c.symbols};peaks={s:1000. for s in c.symbols};drawdowns={s:0. for s in c.symbols};choices=Counter();initial=worker.call('status') if worker else None
    for i in valid_indices:
        for j,s in enumerate(c.symbols):
            if not features.valid(i,j):continue
            a=portfolios[s];price=c.data['close'][i,j];before=a.value(price);exposure=a.exposure(price);vector=features.vector(i,j,exposure);stamp=int(c.times[i])+DAY
            support=memory.query(vector,s,stamp) if memory else None
            if worker:
                neural_values=worker.call('values',vectors=[vector],decision_at=stamp)['values'][0]
                values=[.75*q+.25*r for q,r in zip(neural_values,support['mean_rewards'])] if support and support['count']>=16 and support['mean_squared_distance']<=1 else neural_values
                # A reserve of 10 bps in expected period log-return; hold/cash are
                # explicit alternatives. No argmax over realized future returns.
                action=int(np.argmax(values))
                if values[action]<=.10:action=1 if exposure>.01 and values[1]>=values[0] else 0
            elif fixed=='pattern_memory_only':
                values=support['mean_rewards'] if support and support['count']>=16 and support['mean_squared_distance']<=1 else [0.]*len(ACTIONS)
                action=int(np.argmax(values)) if max(values)>.10 else 0
            else:values=None;action=ACTIONS.index(fixed)
            target=features.targets(i,j,exposure)[action];old_costs=a.costs
            if not all(np.isfinite(c.data[k][i+1:i+STEP+1,j]).all() for k in c.data):raise ValueError('Missing future execution/outcome observation, no silent sample removal')
            interim=a.value(c.data['close'][i+1,j]);peaks[s]=max(peaks[s],interim);drawdowns[s]=max(drawdowns[s],1-interim/peaks[s])
            a.trade(target,c.data['open'][i+2,j])
            for day in range(i+2,i+STEP+1):
                mark=a.value(c.data['close'][day,j]);peaks[s]=max(peaks[s],mark);drawdowns[s]=max(drawdowns[s],1-mark/peaks[s])
            after=a.value(c.data['close'][i+STEP,j]);choices[ACTIONS[action]]+=1
            ledger.record({'phase':label,'symbol':s,'decision_at':stamp,'execution_at':int(c.times[i+2]),'outcome_at':int(c.times[i+STEP])+DAY,'vector':vector,'action':ACTIONS[action],'predicted_values':values,'target_exposure':target,'equity_before':before,'equity_after':after,'period_costs':a.costs-old_costs,'matured_net_return':after/before-1,'similar_pattern_evidence':support,'live_orders':False})
    per_symbol={}
    last=valid_indices[-1]+STEP if valid_indices else None
    for j,s in enumerate(c.symbols):
        a=portfolios[s]
        if last is not None:a.trade(0.,c.data['close'][last,j])
        drawdowns[s]=max(drawdowns[s],1-a.cash/max(peaks[s],1e-12))
        per_symbol[s]={'starting_equity':1000.,'ending_equity':a.cash,'net_return':a.cash/1000-1,'drawdown':drawdowns[s],'costs':a.costs,'orders':a.orders}
    final=worker.call('status') if worker else None
    if worker and initial!=final:raise AssertionError('Frozen development evaluation mutated actual BEAN state')
    ledger.snapshot();result={'label':label,'equal_weight_mean_return':float(np.mean([v['net_return'] for v in per_symbol.values()])),'worst_asset_drawdown':max(v['drawdown'] for v in per_symbol.values()),'per_symbol':per_symbol,'action_counts':dict(choices),'state':final,'periods':len(valid_indices),'scope':'Already available historical development diagnostics, not unseen external performance'}
    print(canonical({'event':'curriculum_evaluation','label':label,'mean_return':result['equal_weight_mean_return']}),flush=True);return result

def select(trials):
    return sorted(trials,key=lambda x:(-min(x['fold_returns']),-float(np.mean(x['fold_returns'])),x['budget'],x['seed']))[0]

def run(candles,core,output):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Refuse to overwrite prior training')
    output.mkdir(parents=True);c=load_candles(candles);features=Features(c);ledger=DecisionLedger(output/'decisions.sqlite3')
    folds=[('2023-04-01T00:00:00Z','2023-10-01T00:00:00Z'),('2024-04-01T00:00:00Z','2024-10-01T00:00:00Z')]
    final_start='2025-04-01T00:00:00Z';final_end='2025-10-01T00:00:00Z'
    protocol={'schema':'aiscend.bean.strategy_curriculum.v1','scope':'DEVELOPMENT_ONLY_PRIOR_HISTORY_SEEN','actions':list(ACTIONS),'seeds':list(SEEDS),'budgets':list(BUDGETS),'folds':folds,'development_audit':[final_start,final_end],'source_rows_sha256':c.source_hash,'source_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'worker_sha256':hashlib.sha256(Path(__file__).with_name('bean_curriculum_worker.py').read_bytes()).hexdigest(),'core_revision':subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip(),'cost_per_side':COST,'initial_equity_per_asset':1000,'decision_step_days':STEP,'execution':'At least one day after feature availability, at next-next bar open; reward only after close of eighth later bar','learner':'Actual BEAN SpongeDQN replay/Adam/Huber, gamma=0 contextual strategy-value regression','teaching':'All actions, cash/half/full initial exposure, all mature positive and negative outcomes within train cutoff; shuffle then alternate <=256 examples per cohort with explicit gradient updates before replay eviction. Independent learner per training budget; no cherry-picked winners','pattern_transfer':'Fixed 75% neural value plus 25% mean reward of 32 nearest mature training patterns on other symbols when at least 16 exist and mean squared normalized distance<=1; else neural values. Expected reward reserve .10 percent log return; otherwise prefer hold/cash. No fitted future scaler.','selection':'Maximize worst of two forward development fold mean net returns; tie mean, lower budget, seed; freeze before development audit','promotion_rule':'Both fold returns positive, development audit positive, beats cash/buy-hold/SMA/Donchian/untrained/pattern-only on audit, worst asset daily-close drawdown<=25%; even a pass is development evidence only','future_promotion':'Require a separately registered new forward or independent dataset evaluation; opened final year cannot count again','narratives':'No point-in-time historical news provided; current headlines excluded','live_orders':False}
    protocol['protocol_sha256']=digest(protocol);(output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');ledger.metadata('protocol',protocol)
    trials={(seed,budget):{'seed':seed,'budget':budget,'fold_returns':[],'folds':[]} for seed in SEEDS for budget in BUDGETS};retained=[]
    all_examples,all_groups=build_memory(features,output/'teaching_memory.sqlite3',int(utc_seconds(final_start)))
    for f,(start,end) in enumerate(folds):
        cutoff=int(utc_seconds(start));examples=[x for x in all_examples if x['outcome_at']<=cutoff]
        for seed in SEEDS:
            order=np.random.default_rng(seed).permutation(len(examples))
            ordered=[examples[k] for k in order]
            # Different budgets need distinct learners: do not train a 256-step
            # candidate on streamed data, then give its 4096-step competitor
            # only the last replay window.
            for budget in BUDGETS:
                name=f'fold-{f}-seed-{seed}-updates-{budget}'
                filename=name+'.sqlite3'
                worker=CandleWorker(core,output/filename,len(examples[0]['vector']),seed,cutoff)
                try:
                    digestion=digest_chunks(worker,ordered,field='experiences',
                                            updates=budget,batch_size=256,
                                            transitions_per_item=1)
                    result=evaluate(features,worker,cutoff,int(utc_seconds(end)),ledger,name,memory=PatternMemory([x for x in all_groups if x['outcome_at']<=cutoff]))
                    worker.call('checkpoint',score=result['equal_weight_mean_return'],samples=result['periods']*len(c.symbols),evaluation_sha256=digest(result),episode=name,protocol_sha256=protocol['protocol_sha256'])
                    trials[(seed,budget)]['fold_returns'].append(result['equal_weight_mean_return']);trials[(seed,budget)]['folds'].append(result)
                    trials[(seed,budget)].setdefault('digestion_receipts',[]).append(digestion)
                    retained.append({'id':name,'file':filename+'.gz','knowledge_cutoff':start,'state':worker.call('status'),'economic_qualification':'DEVELOPMENT_ONLY'})
                finally:worker.close()
    selected=select(list(trials.values()));lock={'selected_seed':selected['seed'],'selected_budget':selected['budget'],'fold_returns':selected['fold_returns'],'protocol_sha256':protocol['protocol_sha256'],'audit_not_yet_opened':True}
    (output/'selection_lock.json').write_text(json.dumps(lock,indent=2)+'\n');ledger.metadata('selection_lock',lock)
    cutoff=int(utc_seconds(final_start));journal=output/'selected.sqlite3';worker=CandleWorker(core,journal,len(all_examples[0]['vector']),selected['seed'],cutoff)
    try:
        order=np.random.default_rng(selected['seed']).permutation(len(all_examples))
        selected_digestion=digest_chunks(worker,[all_examples[k] for k in order],
                                         field='experiences',updates=selected['budget'],
                                         batch_size=256,transitions_per_item=1)
        before=worker.call('status')
        model=evaluate(features,worker,cutoff,int(utc_seconds(final_end)),ledger,'selected/development_audit',memory=PatternMemory(all_groups))
        worker.call('checkpoint',score=model['equal_weight_mean_return'],samples=model['periods']*len(c.symbols),evaluation_sha256=digest(model),episode='selected-development-audit',protocol_sha256=protocol['protocol_sha256'])
        probe=features.vector(len(c.times)-STEP-1,0,0.);probe_time=int(c.times[-STEP-1])+DAY;prediction=worker.call('values',vectors=[probe],decision_at=probe_time)
    finally:worker.close()
    restored=CandleWorker(core,journal,len(probe),selected['seed'],cutoff,restore=True)
    try:
        restart=restored.call('status')==before and restored.call('values',vectors=[probe],decision_at=probe_time)==prediction
        if not restart:raise AssertionError('Actual learned strategy model did not survive restart')
    finally:restored.close()
    baselines={a:evaluate(features,None,cutoff,int(utc_seconds(final_end)),ledger,'baseline/'+a,fixed=a) for a in ('cash','buy_hold','sma_trend','donchian_breakout')}
    baselines['pattern_memory_only']=evaluate(features,None,cutoff,int(utc_seconds(final_end)),ledger,'baseline/pattern_memory_only',fixed='pattern_memory_only',memory=PatternMemory(all_groups))
    cold=CandleWorker(core,output/'untrained.sqlite3',len(probe),selected['seed'],cutoff)
    try:baselines['same_architecture_untrained']=evaluate(features,cold,cutoff,int(utc_seconds(final_end)),ledger,'baseline/same_architecture_untrained',memory=PatternMemory(all_groups))
    finally:cold.close()
    qualifies=all(r>0 for r in selected['fold_returns']) and model['equal_weight_mean_return']>0 and model['worst_asset_drawdown']<=.25 and all(model['equal_weight_mean_return']>x['equal_weight_mean_return'] for x in baselines.values())
    report={'schema':'aiscend.bean.curriculum_results.v1','protocol_sha256':protocol['protocol_sha256'],'development_only':True,'trials':list(trials.values()),'selected':lock,'selected_development_audit':model,'baselines':baselines,'development_qualification_passed':qualifies,'restart_verified':restart,'teaching_examples':len(all_examples),'pattern_groups':len(all_groups),'actual_neural_updates_selected':before['updates'],'selected_digestion':selected_digestion,'selected_state':before,'external_profitability_proven':False,'live_orders':False,'decision_records':ledger.seq,'decision_ledger_tip':ledger.tip}
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');ledger.metadata('results',report);ledger.close()
    retained.append({'id':'selected','file':'selected.sqlite3.gz','knowledge_cutoff':final_start,'state':before,'economic_qualification':'DEVELOPMENT_PASS_REQUIRES_FRESH_TEST' if qualifies else 'NOT_QUALIFIED','diagnostic_vector':probe,'diagnostic_time':probe_time,'diagnostic_values':prediction})
    for p in output.glob('*.sqlite3'):
        with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    for item in retained:item['file_sha256']=hashlib.sha256((output/item['file']).read_bytes()).hexdigest()
    (output/'retention_manifest.json').write_text(json.dumps({'schema':'aiscend.bean.curriculum_retention.v1','policies':retained,'journal_history':'Each budget has a separate journal with a frozen policy capsule. Its SHA-linked recovery is independent of other budgets.'},indent=2)+'\n')
    with (output/'decisions.jsonl').open('rb') as src,gzip.GzipFile(filename=str(output/'decisions.jsonl.gz'),mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    print(canonical({'event':'curriculum_complete','teaching_examples':len(all_examples),'selected':lock,'audit_return':model['equal_weight_mean_return'],'qualification':qualifies,'restart_verified':restart}),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candles',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.candles,a.core_dir,a.output)
