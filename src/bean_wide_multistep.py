"""Actual Core DQN: matched immediate/linked reward learning across Coinbase assets.

Historical and current-vintage research, not a claim of independent profitability.
All inputs, counterfactuals, controls, losses and complete models are retained.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time
import numpy as np
from .bean_causal_simulation import DecisionLedger
from .bean_multistep_environment import VenueAccount,features_for,observation,fixed_action,teaching_rows,withheld,branch,valid_path
from .bean_wide_history import DAY,canonical,save_gzip
from .strategy_atlas import utc_seconds

EXPERTS=('cash','buy_hold','price_trend','donchian_breakout')

def load_snapshot(directory):
    directory=Path(directory);catalog=json.loads((directory/'catalog.json').read_text());manifest=json.loads((directory/'manifest.json').read_text())
    products=catalog['catalog']['eligible_quote_markets']
    if set(products)!=set(manifest['markets']) or manifest['catalog_hash']!=catalog['catalog_hash']:raise ValueError('Incomplete frozen catalog')
    metadata={x['id']:x for x in catalog['raw_catalog'] if isinstance(x,dict) and 'id' in x}
    features={}
    for product in products:
        entry=manifest['markets'][product];blob=(directory/entry['file']).read_bytes()
        if hashlib.sha256(blob).hexdigest()!=entry['sha256']:raise ValueError('Changed candle snapshot')
        body=json.loads(gzip.decompress(blob))
        if body['product']!=product or len(body['candles'])!=entry['candles']:raise ValueError('Wrong market evidence')
        features[product]=features_for(product,body['candles'],catalog['start'],catalog['end'])
    return catalog,manifest,metadata,features

def train_models(core,features,metadata,output,cutoff,protocol_hash):
    import torch
    sys.path.insert(0,str(Path(core).resolve()))
    from bean.optimization.sponge_dqn import SpongeDQN
    from bean.optimization.autodev import Journal
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
    models={name:SpongeDQN(seed=41,gamma=gamma,lr=.0005,batch_size=64,replay_capacity=64,warmup=64,learn_every=10000000,target_sync=100,observation_size=43,actions=('cash','long'),domain='aiscend.wide.multistep.v2') for name,gamma in (('immediate',0.),('linked',.99),('untrained',.99))}
    for model in models.values():model.knowledge_cutoff=cutoff
    initial={name:m.weights_digest() for name,m in models.items()};sampled={name:set() for name in ('immediate','linked')};replay_ids={name:{} for name in sampled};counts=Counter();cohort=[];source_digest=hashlib.sha256();seen=set()
    def digest_batch(batch):
        for name in ('immediate','linked'):
            model=models[name]
            for row in batch:
                if len(model.replay)==64:replay_ids[name].pop(id(model.replay[0]),None)
                model.observe(row['vector'],row['action'],row['reward'],row['next_vector'],row['done']);replay_ids[name][id(model.replay[-1])]=row['id']
            if len(model.replay)==64:
                original=model.rng.sample
                def audited(population,k):
                    selected=original(population,k);sampled[name].update(replay_ids[name][id(x)] for x in selected);return selected
                model.rng.sample=audited
                try:model.train_batch()
                finally:model.rng.sample=original
    training_dir=output/'teaching';training_dir.mkdir()
    for product in sorted(features):
        if withheld(product) or not product.endswith('-USD'):continue
        count=0
        with gzip.GzipFile(filename=str(training_dir/(product+'.jsonl.gz')),mode='wb',mtime=0) as dest:
            for row in teaching_rows(product,features[product],metadata[product],cutoff,EXPERTS,initial_capitals=(1000.,1000./sum(p.endswith('-USD') for p in features))):
                if row['outcome_at']>cutoff or withheld(row['product']) or row['decision_at']>=row['outcome_at']:raise AssertionError('Future or withheld teaching evidence')
                if row['id'] in seen:raise AssertionError('Duplicate teaching row')
                seen.add(row['id']);raw=(canonical(row)+'\n').encode();source_digest.update(raw);dest.write(raw);count+=1
                counts['terminal' if row['done'] else 'nonterminal']+=1;cohort.append(row)
                if len(cohort)==64:digest_batch(cohort);cohort=[]
        counts['markets_with_teaching']+=int(count>0)
        print(canonical(dict(event='market_taught',product=product,rows=count)),flush=True)
    if cohort:digest_batch(cohort)
    if len(seen)<64:raise ValueError('Insufficient actual teaching data')
    retained=[]
    for name,model in models.items():
        if name!='untrained' and sampled[name]!=seen:raise AssertionError('Some supplied rows never participated in a gradient')
        state=model.training_state();raw=canonical(state).encode();path=output/(name+'.training_state.json.gz');save_gzip(path,state)
        recovered=SpongeDQN.from_training_state(json.loads(gzip.decompress(path.read_bytes())))
        if canonical(recovered.training_state()).encode()!=raw:raise AssertionError('Incomplete Core recovery')
        entry=dict(name=name,cutoff=cutoff,recorded_at=int(time.time()),input_rows=model.frames,sampled_rows=len(sampled.get(name,())),updates=model.updates,replay=len(model.replay),state_file=path.name,state_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),state_json_sha256=hashlib.sha256(raw).hexdigest(),weights_sha256=model.weights_digest(),weights_changed=model.weights_digest()!=initial[name],recovered=True,qualification='HISTORICAL_RESEARCH_ONLY',source_rows_sha256=source_digest.hexdigest(),protocol_sha256=protocol_hash)
        journal=Journal(output/(name+'.sqlite3'));entry['journal_tip']=journal.append(dict(objective='aiscend.wide.multistep.v1',iteration=0,**entry));journal.close();retained.append(entry)
    return models,dict(rows=len(seen),**counts),retained

def evaluate(features,metadata,products,start,end,name,ledger,*,model=None,reference='price_trend'):
    """One real funded 1,000-USD allocation for this explicit asset cohort."""
    if model and (not hasattr(model,'knowledge_cutoff') or start<model.knowledge_cutoff):raise ValueError('Future or unknown model knowledge cutoff')
    if not products:return dict(name=name,status='EMPTY_ASSET_COHORT',starting_equity=1000.,ending_equity=1000.,net_return=0.,markets=0)
    if model:import torch
    allocations={p:VenueAccount(1000./len(products),metadata[p]) for p in products};initial=model.weights_digest() if model else None;model_updates=model.updates if model else None
    peaks=1000.;drawdown=0.;marks=[];missing=0;decisions=0;pending={};open_cases={}
    for day in range(start,end,DAY):
        # Decision at this UTC boundary uses only yesterday's completed candle.
        if (day-start)%(7*DAY)==0:
            ready=[]
            for p in products:
                f=features[p];i=int((day-int(f.c.times[0]))//DAY)-1
                if 200<=i<len(f.c.times) and f.valid(i,0):
                    ready.append((p,i,observation(f,i,allocations[p])))
            predictions=None
            if model and ready:
                with torch.no_grad():predictions=model.online(torch.tensor([x[2] for x in ready],dtype=torch.float32)).tolist()
            for k,(p,i,vector) in enumerate(ready):
                f=features[p];account=allocations[p];values=predictions[k] if predictions else None
                action=int(np.argmax(values)) if values else fixed_action(name,f,i,account)
                ref=fixed_action(reference,f,i,account)
                pending[p]=dict(product=p,signal_index=i,decision_at=day,execution_at=day+DAY,outcome_at=day+7*DAY,action=action,reference_action=ref,q_values=values,vector=vector,wealth_before=account.liquidation(float(f.c.data['close'][i,0])),before=copy.deepcopy(account),old_costs=account.costs)
        for p in products:
            account=allocations[p];f=features[p];i=int((day-int(f.c.times[0]))//DAY)
            op=float(f.c.data['open'][i,0]);close=float(f.c.data['close'][i,0])
            case=pending.get(p)
            if case and case['execution_at']==day:
                if math.isfinite(op):case['paid_at_fill']=account.trade(case['action'],op);case['fill_price']=op
                else:case['execution_rejected']='MISSING_OPEN'
                open_cases[p]=case;pending.pop(p)
            if account.quantity:account.age+=1
            if not math.isfinite(close):missing+=1
            case=open_cases.get(p)
            if case and case['outcome_at']<=day+DAY:
                after=account.liquidation(close if math.isfinite(close) else 0.)
                row={k:v for k,v in case.items() if k not in ('before','signal_index')}
                row.update(phase=name,wealth_after=after,paid_costs=account.costs-case['old_costs'],wealth_change=after-case['wealth_before'],current_order_rejections=dict(account.rejects))
                if valid_path(f,case['signal_index'],case['signal_index']+7):
                    _,counter=branch(f,case['signal_index'],case['before'],case['reference_action'])
                    _,actual=branch(f,case['signal_index'],case['before'],case['action'])
                    if abs(actual['wealth_after']-after)>1e-7:raise AssertionError('Execution and source transition differ')
                    if observation(f,case['signal_index']+7,account)!=actual['next_vector']:raise AssertionError('Execution and teaching portfolio states differ')
                    row.update(attribution=actual['effects'],matched_reference=reference,reference_wealth_after=counter['wealth_after'],incremental_wealth=after-counter['wealth_after'],outcome_reason='FEES_DOMINATE' if actual['effects']['paid_costs']<0 and sum(v for k,v in actual['effects'].items() if k!='paid_costs')>=0 and after<case['wealth_before'] else ('POSITIVE_NET_OUTCOME' if after>case['wealth_before'] else 'NONPOSITIVE_MARKET_AND_COST_OUTCOME'))
                else:row['attribution_unavailable']='Missing observed candle; no synthetic fill or price attribution'
                ledger.record(row);decisions+=1;open_cases.pop(p)
        equity=sum(a.liquidation(float(features[p].c.data['close'][int((day-int(features[p].c.times[0]))//DAY),0])) if math.isfinite(float(features[p].c.data['close'][int((day-int(features[p].c.times[0]))//DAY),0])) else a.cash for p,a in allocations.items())
        peaks=max(peaks,equity);drawdown=max(drawdown,1-equity/peaks);marks.append((day+DAY,equity))
    market_outcomes={}
    for p,a in allocations.items():
        f=features[p];i=int((end-DAY-int(f.c.times[0]))//DAY);close=float(f.c.data['close'][i,0])
        if math.isfinite(close):a.trade(0,close)
        market_outcomes[p]=dict(initial_equity=a.initial,ending_cash=a.cash,net_return=a.cash/a.initial-1,costs=a.costs,orders=a.orders,unliquidated_quantity=a.quantity,rejections=a.rejects,withheld_from_training=withheld(p))
    ending=sum(a.cash for a in allocations.values());drawdown=max(drawdown,1-ending/peaks)
    if model and (model.weights_digest()!=initial or model.updates!=model_updates):raise AssertionError('Evaluation changed the frozen learner')
    for p,case in open_cases.items():ledger.record(dict(phase=name,product=p,decision_at=case['decision_at'],status='RIGHT_CENSORED_AT_WINDOW_END',action=case['action'],q_values=case['q_values']))
    row=dict(name=name,status='HISTORICAL_SIMULATION',start=start,end=end,markets=len(products),starting_equity=1000.,ending_equity=ending,net_return=ending/1000.-1,drawdown=drawdown,paid_costs=sum(a.costs for a in allocations.values()),orders=sum(a.orders for a in allocations.values()),matured_decisions=decisions,missing_daily_marks=missing,market_outcomes=market_outcomes,marks_sha256=hashlib.sha256(canonical(marks).encode()).hexdigest(),weights_sha256=initial)
    print(canonical(dict(event='wide_evaluation',name=name,markets=len(products),net_return=row['net_return'],orders=row['orders'])),flush=True)
    return row

def run(history,core,protocol_file,output):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve prior evidence')
    output.mkdir(parents=True);protocol=json.loads(Path(protocol_file).read_text());protocol_hash=hashlib.sha256(canonical(protocol).encode()).hexdigest()
    if subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()!=protocol['core']:raise ValueError('Changed Core revision')
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    catalog,manifest,metadata,features=load_snapshot(history);cutoff=int(utc_seconds(protocol['training_end']));validation_end=int(utc_seconds(protocol['validation_end']));end=int(utc_seconds(protocol['history_end']))
    if catalog['end']!=end:raise ValueError('Changed evaluation end')
    usd=[p for p in sorted(features) if p.endswith('-USD')];known=[p for p in usd if not withheld(p)];unseen=[p for p in usd if withheld(p)]
    ledger=DecisionLedger(output/'decisions.sqlite3');ledger.metadata('protocol',protocol)
    phase_receipts=[]
    def phase(label,products,start,stop,method,*,model=None,reference='price_trend'):
        path=output/'ledgers'/(label+'.sqlite3');local=DecisionLedger(path)
        local.metadata('scope',dict(label=label,protocol_sha256=protocol_hash,products=products,start=start,end=stop))
        result=evaluate(features,metadata,products,start,stop,method,local,model=model,reference=reference)
        receipt=dict(label=label,records=local.seq,tip=local.tip,path=str(path.relative_to(output))+'.gz');local.close()
        for source in (path,path.with_suffix('.jsonl')):
            with source.open('rb') as src,gzip.GzipFile(filename=str(source)+'.gz',mode='wb',mtime=0) as dest:
                import shutil;shutil.copyfileobj(src,dest)
            source.unlink()
        receipt['sha256']=hashlib.sha256(Path(str(path)+'.gz').read_bytes()).hexdigest();phase_receipts.append(receipt);ledger.record(receipt)
        return result
    scope=dict(catalog_markets=len(features),usd_funded_markets=len(usd),known_assets=len(known),withheld_assets=len(unseen),usdc_collected_not_funded=[p for p in features if not p.endswith('-USD')],candles=manifest['candles'],market_data_status_counts=dict(Counter(x['status'] for x in manifest['markets'].values())),listing_scope=catalog['listing_scope'])
    ledger.metadata('scope',scope)
    models,training,retained=train_models(core,features,metadata,output,cutoff,protocol_hash)
    (output/'retention_manifest.json').write_text(json.dumps(dict(policies=retained,training=training,scope=scope),indent=2)+'\n')
    # Freeze the rule control using training dates and training assets only.
    train_start=max(catalog['start']+201*DAY,int(utc_seconds('2022-10-01T00:00:00Z')))
    controls={p:phase('training-'+p,known,train_start,cutoff,p) for p in EXPERTS}
    teacher=sorted(EXPERTS,key=lambda p:(-controls[p]['net_return'],p))[0]
    lock=dict(selected_expert=teacher,training_returns={p:r['net_return'] for p,r in controls.items()},cutoff=cutoff,withheld_assets_used=False,eval_returns_opened=False,model_hashes={p:m.weights_digest() for p,m in models.items()},recorded_at=int(time.time()),protocol_sha256=protocol_hash)
    (output/'selection_lock.json').write_text(json.dumps(lock,indent=2)+'\n');ledger.metadata('selection_lock',lock)
    results=[]
    for period,start,stop in (('validation',cutoff,validation_end),('historical_audit',validation_end,end)):
        for cohort,products in (('known_assets',known),('withheld_assets',unseen),('all_usd_assets',usd)):
            rows={}
            for p in EXPERTS:rows[p]=phase(period+'-'+cohort+'-'+p,products,start,stop,p,reference=teacher)
            rows['training_selected_expert']=copy.deepcopy(rows[teacher])
            for name,model in models.items():rows[name]=phase(period+'-'+cohort+'-'+name,products,start,stop,name,model=model,reference=teacher)
            results.append(dict(period=period,cohort=cohort,results=rows,linked_vs_immediate=rows['linked']['net_return']-rows['immediate']['net_return'],linked_vs_expert=rows['linked']['net_return']-rows[teacher]['net_return']))
            (output/'progress.json').write_text(json.dumps(results,indent=2)+'\n')
    report=dict(schema='aiscend.bean.wide_multistep.results.v1',protocol_sha256=protocol_hash,scope=scope,training=training,selection=lock,results=results,decision_records=sum(x['records'] for x in phase_receipts),phase_receipts=phase_receipts,phase_ledger_tip=ledger.tip,independent_profitability_proven=False,best_on_market_proven=False,live_orders=False)
    ledger.metadata('results',report);ledger.close();(output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    import torch,pandas
    (output/'environment.json').write_text(json.dumps(dict(python=sys.version,platform=platform.platform(),torch=torch.__version__,numpy=np.__version__,pandas=pandas.__version__),indent=2)+'\n')
    con=sqlite3.connect(output/'outcomes.sqlite3');con.execute('CREATE TABLE outcomes(period TEXT,cohort TEXT,method TEXT,return REAL,evidence_json TEXT,PRIMARY KEY(period,cohort,method))')
    for r in results:
        for method,value in r['results'].items():con.execute('INSERT INTO outcomes VALUES (?,?,?,?,?)',(r['period'],r['cohort'],method,value['net_return'],canonical(value)))
    con.commit()
    if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise AssertionError('Outcome database corrupted')
    con.close()
    for path in output.iterdir():
        if path.suffix in ('.sqlite3','.jsonl'):
            with path.open('rb') as source,gzip.GzipFile(filename=str(path)+'.gz',mode='wb',mtime=0) as dest:
                import shutil;shutil.copyfileobj(source,dest)
    lines=['# BEAN broad Coinbase multi-step experiment\n','Historical current-survivor research. Later periods and held-out base assets were excluded from this model training, but these histories have appeared in earlier research. There is no independent forward-performance or market-rank claim. All losses remain in the SQL index and original decision ledger.\n',f"Attempted {scope['catalog_markets']} quote markets; {scope['candles']:,} actual daily candles. Funded USD portfolios use {len(usd)} markets, including {len(unseen)} base assets withheld from all gradient inputs. USDC histories are retained but do not receive an assumed USD conversion. Every portfolio below starts with one actual modeled $1,000 split across its entire stated cohort; these are alternative simulations, not simultaneous accounts. Current metadata and missing history can prevent trades.\n",f"Teaching rows: {training['rows']:,}; nonterminal: {training.get('nonterminal',0):,}. Both trained Core models sampled every supplied row. Full optimizer, target, replay and RNG states recovered exactly. The new 43-input trade-state model includes actual starting/cash capital and order affordability, using the same verified Core, not incompatible weights from the older 47-input task. Episodes alternate the actual whole-universe sleeve budget and 1,000 USD.\n",'| Period | Cohort | Cash | Buy/hold | Frozen expert | Untrained | Immediate DQN | Linked DQN |','| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in results:lines.append('| '+r['period']+' | '+r['cohort']+' | '+' | '.join(f"{r['results'][p]['net_return']:+.2%}" for p in ('cash','buy_hold','training_selected_expert','untrained','immediate','linked'))+' |')
    lines+=['\nThe selected expert is **'+teacher+'**, chosen once from training-only net returns. Learned decisions retain action values and a matched reference action using identical initial account, fill, costs and outcome. The reward ledger reconciles pre-fill market movement, post-fill movement, paid costs and exit-reserve changes; this is mechanical explanation, not evidence of semantic or causal understanding.\n','Missing marks are conservatively zero until an actual candle returns. There are no interpolated candles or retroactive exits before gaps. Unliquidatable terminal dust is not credited as cash. Current listing/order metadata are not historical venue rules or account permissions. One percent per-side execution costs are a stress assumption, not measured fills.\n','The next independent test must collect new observations after model selection and keep every candidate frozen. Repeated historical runs are development, even if they become profitable. No live trading was enabled.\n']
    (output/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    files={str(p.relative_to(output)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(output.rglob('*')) if p.is_file() and p.suffix not in ('.sqlite3','.jsonl')}
    (output/'artifacts_manifest.json').write_text(json.dumps(dict(files=files),indent=2)+'\n');print(canonical(dict(event='wide_multistep_complete',scope=scope,training=training)),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--history',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--protocol',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.history,a.core_dir,a.protocol,a.output)
