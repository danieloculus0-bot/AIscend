"""Isolate replay coverage and economic consistency before asking for more training.

Every historical interval here is development history already inspected. A
profitable expert fallback is not credited as a learned neural advantage.
"""
from __future__ import annotations
from collections import Counter
import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np
from .bean_strategy_curriculum import ACTIONS,Account,Features,load_candles,experience
from .bean_economic_values import choose,funded_values,market_vector,gate_overrides
from .bean_causal_simulation import DecisionLedger
from .strategy_atlas import canonical,digest,utc_seconds

class Worker:
    def __init__(self,core,journal,scheme,cutoff,capacity):
        env={k:v for k,v in os.environ.items() if k in ('PATH','PYTHONPATH','LD_LIBRARY_PATH','LANG')}
        self.p=subprocess.Popen([sys.executable,'-m','src.bean_economic_worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,env=env)
        self.call('init',core_dir=str(Path(core).resolve()),journal=str(journal),scheme=scheme,cutoff=cutoff,capacity=capacity,actions=list(ACTIONS))
    def call(self,op,**kw):
        self.p.stdin.write(canonical({'op':op,**kw})+'\n');self.p.stdin.flush();line=self.p.stdout.readline()
        if not line:raise RuntimeError(self.p.stderr.read()[-3000:])
        x=json.loads(line)
        if not x['ok']:raise RuntimeError(x['error'])
        return x['result']
    def close(self):
        if self.p.poll() is None:self.call('close');self.p.wait(timeout=15)
        for x in (self.p.stdin,self.p.stdout,self.p.stderr):x.close()

def examples(features,cutoff):
    raw=[];market=[];c=features.c
    for i in range(200,len(c.times)-8,8):
        if int(c.times[i+8])+86400>cutoff:continue
        for j,s in enumerate(c.symbols):
            if not features.valid(i,j):continue
            if not all(np.isfinite(c.data[k][i+1:i+9,j]).all() for k in c.data):raise ValueError('Unknown outcome cannot be dropped silently')
            decision=int(c.times[i])+86400;outcome=int(c.times[i+8])+86400
            for w in (0.,.5,1.):
                for a in range(len(ACTIONS)):
                    x=experience(features,i,j,w,a);raw.append({'id':digest([s,decision,w,a]),'decision_at':decision,'outcome_at':outcome,'vector':x['vector'],'action':a,'reward':x['reward']})
            vector=market_vector(features.vector(i,j,0.));p=c.data['close'][i,j];fill=c.data['open'][i+2,j];end=c.data['close'][i+8,j]
            for a,reward in enumerate((100*math.log(fill/p),100*math.log(end/fill))):market.append({'id':digest([s,decision,'market',a]),'decision_at':decision,'outcome_at':outcome,'vector':vector,'action':a,'reward':reward})
    return raw,market

def evaluate(features,start,end,ledger,label,*,worker=None,fixed=None,reference='hold',constant_forecast=None):
    c=features.c;capital=1000.;accounts={s:Account(capital/len(c.symbols)) for s in c.symbols};peak=capital;drawdown=0.;choices=Counter();marks=[];predictions=[];costs=[]
    state=worker.call('status') if worker else None;last=None;orders=0
    def mark(k):
        nonlocal peak,drawdown
        value=sum(accounts[s].value(c.data['close'][k,j]) for j,s in enumerate(c.symbols));peak=max(peak,value);drawdown=max(drawdown,1-value/peak);marks.append([int(c.times[k])+86400,value])
    for i in range(200,len(c.times)-8,8):
        if not (start<=int(c.times[i])+86400 and int(c.times[i+8])+86400<=end):continue
        pending=[]
        for j,s in enumerate(c.symbols):
            if not features.valid(i,j):continue
            a=accounts[s];price=c.data['close'][i,j];before=a.value(price);vector=features.vector(i,j,a.exposure(price));forecast=None
            info={'initial_portfolio_capital':capital,'initial_sleeve_capital':capital/len(c.symbols),'equity':before,'cash':a.cash,'marked_position':a.quantity*price,'paid_costs':a.costs}
            if worker:
                response=worker.call('values',vectors=[vector],decision_at=int(c.times[i])+86400,accounts=[info]);values=response['values'][0];forecast=response.get('market_forecasts',[None])[0];action=choose(values,mentor=reference)
            elif constant_forecast is not None:
                forecast=constant_forecast;values=[v['net_log_return'] for v in funded_values(vector,forecast)];action=choose(values,mentor=reference)
            else:values=None;action=ACTIONS.index(fixed)
            target=features.targets(i,j,vector[15])[action];pending.append((j,s,target,before,vector,values,forecast,info,action,a.costs));choices[ACTIONS[action]]+=1
        if not pending:continue
        if any(not np.isfinite(c.data[k][i+1:i+9,j]).all() for j,_,*rest in pending for k in c.data):raise ValueError('Missing execution/outcome observation')
        mark(i+1)
        for j,s,target,*rest in pending:accounts[s].trade(target,c.data['open'][i+2,j])
        for day in range(i+2,i+9):mark(day)
        for j,s,target,before,vector,values,forecast,info,action,old_cost in pending:
            a=accounts[s];after=a.value(c.data['close'][i+8,j]);paid=a.costs-old_cost;costs.append(paid)
            actual=[100*math.log(c.data['open'][i+2,j]/c.data['close'][i,j]),100*math.log(c.data['close'][i+8,j]/c.data['open'][i+2,j])]
            if forecast is not None:predictions.append({'symbol':s,'decision_at':int(c.times[i])+86400,'forecast':forecast,'outcome':actual})
            ledger.record({'phase':label,'symbol':s,'decision_at':int(c.times[i])+86400,'execution_at':int(c.times[i+2]),'outcome_at':int(c.times[i+8])+86400,'account_visible_to_bean':info,'vector':vector,'action':ACTIONS[action],'reference_strategy':reference if worker or constant_forecast is not None else fixed,'target':target,'modeled_values':values,'forecast':forecast,'matured_market_outcome':actual,'equity_before':before,'equity_after':after,'paid_cost':paid,'net_return':after/before-1,'live_orders':False})
        last=i+8
    if last is not None:
        for j,s in enumerate(c.symbols):accounts[s].trade(0.,c.data['close'][last,j])
    ending=sum(a.cash for a in accounts.values());peak=max(peak,ending);drawdown=max(drawdown,1-ending/peak)
    final=worker.call('status') if worker else None
    if worker and state!=final:raise AssertionError('Frozen evaluation changed actual learner state')
    ledger.snapshot();result={'label':label,'starting_equity':capital,'ending_equity':ending,'net_return':ending/capital-1,'portfolio_daily_close_drawdown':drawdown,'paid_costs':sum(a.costs for a in accounts.values()),'orders':sum(a.orders for a in accounts.values()),'action_counts':dict(choices),'state':final,'forecast_cases':predictions,'marks_sha256':digest(marks),'scope':'Eight surviving example instruments, funded $1000 split equally; already inspected development history'}
    print(canonical({'event':'economic_evaluation','label':label,'ending_equity':ending,'net_return':result['net_return']}),flush=True);return result

def forecast_scores(result,training_mean):
    cases=result['forecast_cases']
    if not cases:return None
    y=np.array([x['outcome'] for x in cases]);p=np.array([x['forecast'] for x in cases]);m=np.array(training_mean)
    return {'cases':len(cases),'model_mse':float(np.mean((y-p)**2)),'zero_mse':float(np.mean(y**2)),'past_mean_mse':float(np.mean((y-m)**2)),'beats_past_mean':bool(np.mean((y-p)**2)<np.mean((y-m)**2))}

def best_teacher(results):
    return sorted(results,key=lambda name:(-results[name]['net_return'],name))[0]

def recover(directory,core,entry):
    sys.path.insert(0,str(Path(core).resolve()))
    from bean.optimization.sponge_dqn import SpongeDQN
    raw_path=Path(directory)/entry['state_file'];blob=raw_path.read_bytes()
    if hashlib.sha256(blob).hexdigest()!=entry['state_sha256']:raise ValueError('Retained full training file changed')
    raw=gzip.decompress(blob)
    if hashlib.sha256(raw).hexdigest()!=entry['state_json_sha256']:raise ValueError('Retained training JSON changed')
    model=SpongeDQN.from_training_state(json.loads(raw))
    if model.weights_digest()!=entry['online_weights_sha256']:raise ValueError('Recovered weights differ')
    if digest(model.training_state())!=entry['state_json_sha256']:raise ValueError('Recovered optimizer/replay/RNG state differs')
    return {'weights_match':True,'full_training_state_matches':True,'replay_entries':len(model.replay),'frames':model.frames,'updates':model.updates}

def run(candles,core,output,updates=1024):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve earlier economic experiment')
    output.mkdir(parents=True);features=Features(load_candles(candles));ledger=DecisionLedger(output/'decisions.sqlite3')
    import platform,pandas,torch
    (output/'environment.json').write_text(json.dumps({'python':sys.version,'platform':platform.platform(),'numpy':np.__version__,'pandas':pandas.__version__,'torch':torch.__version__},indent=2)+'\n')
    splits=[('2023-04-01T00:00:00Z','2023-10-01T00:00:00Z'),('2024-04-01T00:00:00Z','2024-10-01T00:00:00Z'),('2025-04-01T00:00:00Z','2025-10-01T00:00:00Z')]
    protocol={'schema':'aiscend.bean.economic_values.v1','scope':'DEVELOPMENT_ONLY_HISTORY_ALREADY_SEEN','source_sha256':features.c.source_hash,'core_revision':subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip(),'implementation':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ('src/bean_economic_experiment.py','src/bean_economic_worker.py','src/bean_economic_values.py')},'splits':splits,'arms':['raw_limited','raw_full','factor'],'seed':41,'updates':updates,'raw_supervision':'All 11 action labels at three prior exposures; legacy 4096 replay versus full replay, identical input order and update budget','factor_supervision':'Two mature market return components per distinct asset/period, 35 market-only inputs. Exact same funded cash/fee engine converts forecasts to action values. Log forecasts are scenarios, not calibrated expected cash returns.','decision_rule':'Choose a different action only when its modeled net log return exceeds the reference by .10 percentage points. All learned arms share this rule.','capital':1000,'allocation':'Equal fixed funded sleeves across eight present-selected symbols; not a full-universe qualification','cost_per_side':.01,'expert_selection':'Best training-only net portfolio return among all 11 fixed experts; alphabetical tie. No later returns read.','override_gate':'Before 2025 audit: economic overlay must beat its training-selected expert on BOTH earlier development folds AND market forecast MSE must beat training-mean forecasts on BOTH. Otherwise retain the expert. No learned profit credit for expert fallback.','limitations':['All dates are previously inspected development history','Eight-symbol survivorship and venue bias','Daily reference-open simulation, no exchange fill claim','No independently audited competitor comparison','More epochs are not more independent market evidence'],'live_orders':False}
    protocol['protocol_sha256']=digest(protocol);(output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');ledger.metadata('protocol',protocol)
    raw,market=examples(features,int(utc_seconds(splits[-1][0])))
    for name,data in (('raw_examples',raw),('distinct_market_examples',market)):
        with gzip.GzipFile(filename=str(output/(name+'.jsonl.gz')),mode='wb',mtime=0) as dest:
            for x in data:dest.write((canonical(x)+'\n').encode())
    results=[];retained=[];advantages=[];forecast_checks=[];gate=False
    for fold,(start,end) in enumerate(splits):
        cutoff=int(utc_seconds(start));stop=int(utc_seconds(end));label=f'fold-{fold}'
        teacher_results={name:evaluate(features,int(features.c.times[200])+86400,cutoff,ledger,label+'/teacher_train/'+name,fixed=name) for name in ACTIONS}
        teacher=best_teacher(teacher_results)
        if fold==2:
            gate=gate_overrides(advantages,forecast_checks);lock={'protocol_sha256':protocol['protocol_sha256'],'overrides_enabled':gate,'prior_fold_advantages':advantages,'prior_forecast_beats_mean':forecast_checks,'selected_expert_from_training_only':teacher,'audit_not_yet_evaluated':True};(output/'selection_lock.json').write_text(json.dumps(lock,indent=2)+'\n');ledger.metadata('selection_lock',lock)
        expert=evaluate(features,cutoff,stop,ledger,label+'/expert_only',fixed=teacher)
        benchmark={a:evaluate(features,cutoff,stop,ledger,label+'/benchmark/'+a,fixed=a) for a in ('cash','buy_hold')}
        models={};cohort_market=[x for x in market if x['outcome_at']<=cutoff];training_mean=[float(np.mean([x['reward'] for x in cohort_market if x['action']==a])) for a in (0,1)]
        for arm in protocol['arms']:
            data=cohort_market if arm=='factor' else [x for x in raw if x['outcome_at']<=cutoff]
            capacity=4096 if arm=='raw_limited' else len(data);worker=Worker(core,output/(label+'-'+arm+'.sqlite3'),arm,cutoff,capacity)
            try:
                before=worker.call('status');order=np.random.default_rng(41).permutation(len(data))
                for k in range(0,len(order),256):worker.call('teach',examples=[data[n] for n in order[k:k+256]])
                coverage=worker.call('train',updates=updates)
                outcome=evaluate(features,cutoff,stop,ledger,label+'/'+arm,worker=worker)
                if arm=='factor':
                    overlay=evaluate(features,cutoff,stop,ledger,label+'/economic_overlay',worker=worker,reference=teacher);models['economic_overlay']=overlay
                    scores=forecast_scores(outcome,training_mean)
                    if fold<2:advantages.append(overlay['net_return']-expert['net_return']);forecast_checks.append(scores['beats_past_mean'])
                    models['forecast_scores']=scores
                checkpoint=worker.call('checkpoint',coverage=coverage,protocol_sha256=protocol['protocol_sha256']);checkpoint['id']=label+'-'+arm;checkpoint['recovery']=recover(output,core,checkpoint);checkpoint['weights_changed']=checkpoint['online_weights_sha256']!=before['weights_sha256'];retained.append(checkpoint)
                models[arm]={'result':outcome,'coverage':coverage}
            finally:worker.close()
        mean_policy=evaluate(features,cutoff,stop,ledger,label+'/past_mean_forecast',constant_forecast=training_mean)
        if fold==2:
            policy=models['economic_overlay'] if gate else expert
            models['expert_first_policy']={'behavior':'learned_overlay' if gate else 'unchanged_expert','result':policy,'neural_incremental_profit_claimed':gate and policy['net_return']>expert['net_return']}
        row={'fold':fold,'start':start,'end':end,'training_selected_expert':teacher,'teacher_training_results':teacher_results,'expert_only':expert,'benchmarks':benchmark,'models':models,'past_mean_policy':mean_policy};results.append(row)
        (output/'progress.json').write_text(json.dumps(results,indent=2)+'\n')
    report={'schema':'aiscend.bean.economic_results.v1','protocol_sha256':protocol['protocol_sha256'],'development_only':True,'raw_input_labels':len(raw),'distinct_asset_periods':len(market)//2,'distinct_market_labels':len(market),'results':results,'overrides_enabled_after_two_folds':gate,'independent_profitability_proven':False,'best_on_market_proven':False,'decision_records':ledger.seq,'decision_ledger_tip':ledger.tip,'live_orders':False};ledger.metadata('results',report);ledger.close();(output/'results.json').write_text(json.dumps(report,indent=2)+'\n');(output/'retention_manifest.json').write_text(json.dumps({'policies':retained},indent=2)+'\n')
    for p in output.glob('*.sqlite3'):
        with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    with (output/'decisions.jsonl').open('rb') as src,gzip.GzipFile(filename=str(output/'decisions.jsonl.gz'),mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    lines=['# BEAN economic consistency and replay coverage\n','This is an already-seen, eight-symbol development ablation. It cannot establish best-on-market performance or independent profitability.\n',f"Raw labels: {len(raw):,}. Distinct asset/period outcomes: {len(market)//2:,}. The raw labels repeat actions and starting exposures; they are not that many independent market events.\n",'| Fold | Training-selected expert | Expert return | Raw 4096 | Raw full replay | Factored values | Economic overlay |','| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for r in results:lines.append(f"| {r['start'][:4]} | {r['training_selected_expert']} | {r['expert_only']['net_return']:+.2%} | {r['models']['raw_limited']['result']['net_return']:+.2%} | {r['models']['raw_full']['result']['net_return']:+.2%} | {r['models']['factor']['result']['net_return']:+.2%} | {r['models']['economic_overlay']['net_return']:+.2%} |")
    last=results[-1]['models']['expert_first_policy'];lines += [f"\nThe predeclared override gate enabled learning overrides: **{gate}**. The final development expert-first policy used **{last['behavior']}**, ending with **${last['result']['ending_equity']:.2f} from $1,000**. When an expert fallback earns money, its return is explicitly attributed to the expert, not to learning.\n",'The factor model predicts two market log-return components. Exact funded trade accounting supplies costs and all 11 action values; cash versus holding cash must match and trades with identical exposures cannot acquire different values from their names. This fixes structural inconsistency, not predictive skill. Forecast MSE is compared with zero and training-mean forecasts.\n','The raw-limited and raw-full arms share the same examples/order/network/updates. Coverage receipts report how many inputs were evicted before training and how many available entries were actually sampled. The complete Core training state (including full replay, optimizer, target and RNG) is hash-linked through external gzip files because the original capsule has a 16 MB expanded-size limit. Every saved state was independently recovered.\n','All earlier failures are preserved in parent branches. No old final year was reopened, no live order was placed, and no result here is called an unseen competitor benchmark. Broader point-in-time asset coverage and genuinely new forward evaluation remain necessary.']
    (output/'RESULTS.md').write_text('\n'.join(lines)+'\n');print(canonical({'event':'economic_experiment_complete','gate':gate,'expert_first_return':last['result']['net_return'],'behavior':last['behavior']}),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candles',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--updates',type=int,default=1024);a=p.parse_args();run(a.candles,a.core_dir,a.output,a.updates)
