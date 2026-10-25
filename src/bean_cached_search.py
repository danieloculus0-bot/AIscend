"""One-pass, multi-candidate BEAN search over the immutable teaching corpus.

Decode each cached source row once, train a fixed seed/gamma grid, compare on an
internal training-selection window, then lock before the later historical audit.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .bean_compact_scores import CompactScores
from .bean_multistep_environment import withheld
from .bean_wide_history import DAY,canonical,save_gzip
from .bean_wide_multistep import load_snapshot,evaluate
from .bean_causal_simulation import DecisionLedger
from .strategy_atlas import utc_seconds

def learn_cached(corpus,core,output,cutoff,seeds):
    import torch
    sys.path.insert(0,str(Path(core).resolve()))
    from bean.optimization.sponge_dqn import SpongeDQN
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
    corpus,output=Path(corpus),Path(output);output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((corpus/'artifacts_manifest.json').read_text())['files'];models={};receipts=[]
    for seed in seeds:
        for gamma in (0.,.99):
            name=f'seed{seed}_gamma{gamma}'
            models[name]=SpongeDQN(seed=seed,gamma=gamma,lr=.0005,batch_size=64,replay_capacity=64,warmup=64,learn_every=10000000,target_sync=100,observation_size=43,actions=('cash','long'),domain='aiscend.wide.multistep.v2')
            models[name].knowledge_cutoff=cutoff
    initial={k:v.weights_digest() for k,v in models.items()};seen=set();batch=[];source=hashlib.sha256();counter=0;updates=0;started=time.perf_counter()
    def digest(rows):
        nonlocal updates
        for model in models.values():
            for row in rows:model.observe(row['vector'],row['action'],row['reward'],row['next_vector'],row['done'])
            if len(model.replay)==64:
                original=model.rng.sample
                def checked(population,k):
                    selected=original(population,k)
                    if {id(x) for x in selected}!={id(x) for x in model.replay}:raise AssertionError('A batch did not include its complete current replay')
                    return selected
                model.rng.sample=checked
                try:model.train_batch()
                finally:model.rng.sample=original
        updates+=1
    for path in sorted((corpus/'teaching').glob('*.jsonl.gz')):
        relative=str(path.relative_to(corpus));blob=path.read_bytes()
        if relative not in manifest or hashlib.sha256(blob).hexdigest()!=manifest[relative]['sha256']:raise ValueError('Cached source differs from its archived evidence')
        for line in gzip.decompress(blob).splitlines():
            row=json.loads(line)
            # Original terminal flags may inspect the next week's data coverage.
            # Purge that entire extra week before the new earlier knowledge cutoff.
            if row['outcome_at']+7*DAY>cutoff:continue
            if withheld(row['product']) or not row['decision_at']<row['outcome_at'] or len(row['vector'])!=43:raise ValueError('Wrong asset, future boundary or observation contract')
            if row['id'] in seen:raise ValueError('Duplicate cached source row')
            seen.add(row['id']);source.update(line+b'\n');counter+=1;batch.append(row)
            if len(batch)==64:digest(batch);batch=[]
    if batch:digest(batch)
    if counter<64:raise ValueError('Insufficient mature cached data')
    for name,model in models.items():
        state=model.training_state();path=output/(name+'.training_state.json.gz');save_gzip(path,state)
        recovered=SpongeDQN.from_training_state(json.loads(gzip.decompress(path.read_bytes())))
        if recovered.training_state()!=state:raise AssertionError('Cached-search Core recovery differs')
        receipts.append(dict(name=name,cutoff=cutoff,rows=counter,updates=model.updates,sampled_all_source_rows=True,weights_changed=model.weights_digest()!=initial[name],weights_sha256=model.weights_digest(),state_file=path.name,state_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),full_state_recovered=True))
    return models,dict(source_rows=counter,source_sha256=source.hexdigest(),candidate_count=len(models),gradient_updates_per_candidate=next(iter(models.values())).updates,seconds=time.perf_counter()-started),receipts

def run(history,corpus,core,output,seeds):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve earlier searches')
    output.mkdir(parents=True);protocol=json.loads((Path(corpus)/'protocol.json').read_text())
    revision=subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()
    if revision!=protocol['core']:raise ValueError('Changed actual Core')
    start=int(utc_seconds('2025-04-01T00:00:00Z'));stop=int(utc_seconds(protocol['training_end']));end=int(utc_seconds(protocol['history_end']))
    config=dict(schema='aiscend.bean.cached_search.v1',source_protocol_sha256=hashlib.sha256(canonical(protocol).encode()).hexdigest(),source_results_ref='4d9f3bdf22071febe1d23ac62a20402e8119fb03',core=revision,seeds=seeds,gammas=[0.,.99],gradient_training_end=start,label_availability_guard_days=7,internal_training_selection_end=stop,audit_end=end,qualification='Previously researched historical development data; not independent forward evidence',ranking='Incremental net return over fixed price_trend, then lower drawdown, then name; cash and fixed controls also retained',live_orders=False)
    (output/'protocol.json').write_text(json.dumps(config,indent=2)+'\n')
    catalog,manifest,metadata,features=load_snapshot(history);usd=sorted(p for p in features if p.endswith('-USD'));known=[p for p in usd if not withheld(p)];unseen=[p for p in usd if withheld(p)]
    models,training,receipts=learn_cached(corpus,core,output,start,seeds)
    # Keep the unchanged random network as an explicit control, not learned profit.
    from bean.optimization.sponge_dqn import SpongeDQN
    untrained_path=Path(corpus)/'untrained.training_state.json.gz';blob=untrained_path.read_bytes()
    expected=json.loads((Path(corpus)/'artifacts_manifest.json').read_text())['files'][untrained_path.name]['sha256']
    if hashlib.sha256(blob).hexdigest()!=expected:raise ValueError('Changed untrained control')
    control=SpongeDQN.from_training_state(json.loads(gzip.decompress(blob)))
    if control.frames or control.updates:raise ValueError('The random control already learned')
    control.knowledge_cutoff=start;models['untrained_seed41']=control
    (output/'untrained_seed41.training_state.json.gz').write_bytes(blob)
    (output/'retention_manifest.json').write_text(json.dumps(dict(training=training,policies=receipts),indent=2)+'\n')
    scores=CompactScores(output/'compact_scores');results=[];phase_receipts=[]
    def phase(label,products,left,right,name,model=None):
        ledger=DecisionLedger(output/'ledgers'/(label+'.sqlite3'))
        ledger.metadata('scope',dict(label=label,products=products,start=left,end=right,configuration=config))
        result=evaluate(features,metadata,products,left,right,name,ledger,model=model,reference='price_trend');path=ledger.path;receipt=dict(path=str(path.relative_to(output))+'.gz',tip=ledger.tip,records=ledger.seq);ledger.close()
        for p in (path,path.with_suffix('.jsonl')):
            with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
            p.unlink()
        receipt['sha256']=hashlib.sha256(Path(str(path)+'.gz').read_bytes()).hexdigest();phase_receipts.append(receipt)
        return result,receipt
    selection={};selection_receipts={}
    for name in ('cash','buy_hold','price_trend'):
        selection[name],selection_receipts[name]=phase('selection-'+name,known,start,stop,name)
    for name,model in models.items():selection[name],selection_receipts[name]=phase('selection-'+name,known,start,stop,name,model)
    for name,result in selection.items():
        keys=dict(method=name,dataset=catalog['catalog_hash'],window=dict(phase='internal_training_selection',start=start,end=stop,assets='known'),seed=None if name not in models else models[name].seed,cost=.01,configuration=config,evidence=selection_receipts[name])
        scores.record(keys=keys,candidate=result,baseline=selection['price_trend'],role='training',available_at=stop,source=dict(qualification='Internal selection inside the original training corpus',**selection_receipts[name]))
    ranked=scores.rank(int(time.time()),window=dict(phase='internal_training_selection',start=start,end=stop,assets='known'),limit=100)
    if {r['method'] for r in ranked}!=set(selection):raise AssertionError('Compact score table lost a candidate')
    selected=ranked[0]['method']
    expected=sorted(selection,key=lambda n:(-selection[n]['net_return'],selection[n]['drawdown'],n))[0]
    if selected!=expected:raise AssertionError('Compact ranking differs from the declared exact objective')
    lock=dict(selected=selected,selection_source='CompactScores.rank SQL index',score_mnemonic=ranked[0]['mnemonic'],score_receipt=ranked[0]['receipt'],selection_returns={n:r['net_return'] for n,r in selection.items()},selection_end=stop,later_audit_opened=False,withheld_assets_used_in_selection=False,learned_policy_selected=selected.startswith('seed'),weights_sha256=models[selected].weights_digest() if selected in models else None)
    (output/'selection_lock.json').write_text(json.dumps(lock,indent=2)+'\n')
    for cohort,products in (('all_usd',usd),('withheld_assets',unseen)):
        group={};references={}
        for name in dict.fromkeys(('cash','buy_hold','price_trend',selected)):
            group[name],references[name]=phase('audit-'+cohort+'-'+name,products,stop,end,name,models.get(name))
        for name,result in group.items():
            keys=dict(method=name,dataset=catalog['catalog_hash'],window=dict(phase='later_historical_audit',cohort=cohort,start=stop,end=end),seed=None if name not in models else models[name].seed,cost=.01,configuration=config,evidence=references[name])
            scores.record(keys=keys,candidate=result,baseline=group['price_trend'],role='evaluation',available_at=end,source=references[name])
        results.append(dict(cohort=cohort,results=group))
    scores.close()
    report=dict(protocol=config,training=training,selection=lock,selection_results=selection,results=results,phase_receipts=phase_receipts,live_orders=False,independent_profitability_proven=False)
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    files={str(p.relative_to(output)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in output.rglob('*') if p.is_file()}
    (output/'artifacts_manifest.json').write_text(json.dumps(dict(files=files),indent=2)+'\n');print(canonical(dict(event='cached_search_complete',training=training,selected=selected,audit_returns={r['cohort']:r['results'][selected]['net_return'] for r in results})),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--history',type=Path,required=True);p.add_argument('--corpus',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--seeds',nargs='+',type=int,default=[7,41]);a=p.parse_args();run(a.history,a.corpus,a.core_dir,a.output,sorted(set(a.seeds)))
