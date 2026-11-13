"""Controlled, retained BEAN teaching: order, matched advantage and real recall."""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import numpy as np
from .bean_cached_search import learn_cached
from .bean_compact_scores import CompactScores
from .bean_causal_simulation import DecisionLedger
from .bean_retained_lessons import digest,load_lessons,save_lessons,active_memory,RetainedMemory,RecallPolicy
from .bean_multistep_environment import withheld
from .bean_wide_multistep import load_snapshot,evaluate
from .bean_wide_history import canonical,save_gzip
from .strategy_atlas import utc_seconds

def sentinel_indices(size):
    rng=np.random.default_rng(19);indices=[]
    for group in np.array_split(np.arange(size),4):
        if len(group):indices.extend(rng.choice(group,min(128,len(group)),replace=False).tolist())
    return indices

def diagnostic(model,lessons,indices):
    import torch
    if not indices:raise ValueError('No retention diagnostic cases')
    with torch.no_grad():q=model.online(torch.tensor([lessons[i]['vector'] for i in indices],dtype=torch.float32)).numpy()
    truth=np.array([lessons[i]['rewards'][1] for i in indices]);prediction=q[:,1]-q[:,0]
    parts={}
    for number,indexes in enumerate(np.array_split(np.arange(len(indices)),4)):
        if len(indexes):parts[str(number)]=float(np.mean((prediction[indexes]-truth[indexes])**2))
    return dict(cases=len(indices),matched_advantage_mse=float(np.mean((prediction-truth)**2)),direction_accuracy=float(np.mean((prediction>0)==(truth>0))),always_cash_direction_accuracy=float(np.mean(truth<=0)),source_order_quartile_mse=parts,qualification='Repeated training-case fitting/retention diagnostic, not held-out predictive skill')

def train_order(lessons,reference,mode,output,cutoff):
    """Same input identities, Core architecture, seed and one full batch per cohort."""
    from bean.optimization.sponge_dqn import SpongeDQN
    config=copy.deepcopy(reference.config)
    if mode=='paired_advantage':config['domain']='aiscend.wide.paired_advantage.v1'
    model=SpongeDQN(**config);model.knowledge_cutoff=cutoff
    seed=model.seed;order=np.arange(len(lessons))
    if mode!='grouped_raw':order=np.random.default_rng(seed).permutation(order)
    indices=sentinel_indices(len(lessons));before=diagnostic(model,lessons,indices);initial=model.weights_digest();curve=[];source_ids=set();sampled=set();replay_ids={};pending=[];order_hash=hashlib.sha256();started=time.perf_counter()
    expected=(len(lessons)*2+63)//64;checkpoints={max(1,(expected*q+3)//4) for q in (1,2,3,4)}
    def update(batch):
        for row,reward in batch:
            if row['outcome_at']+7*86400>cutoff:raise ValueError('Unmatured lesson crossed training cutoff')
            if len(model.replay)==64:replay_ids.pop(id(model.replay[0]),None)
            model.observe(row['vector'],row['action'],reward,row['next_vector'],row['done']);replay_ids[id(model.replay[-1])]=row['id'];source_ids.add(row['id']);order_hash.update(row['id'].encode())
        if len(model.replay)==64:
            original=model.rng.sample
            def audited(population,k):
                result=original(population,k);sampled.update(replay_ids[id(x)] for x in result);return result
            model.rng.sample=audited
            try:model.train_batch()
            finally:model.rng.sample=original
            if model.updates in checkpoints:curve.append(dict(updates=model.updates,frames=model.frames,diagnostic=diagnostic(model,lessons,indices)))
    for i in order:
        lesson=lessons[int(i)]
        for action,row in enumerate(lesson['raw_rows']):
            pending.append((row,lesson['rewards'][action] if mode=='paired_advantage' else row['reward']))
            if len(pending)==64:update(pending);pending=[]
    if pending:update(pending)
    if model.frames!=2*len(lessons) or sampled!=source_ids or model.updates!=expected:raise AssertionError('Matched budget or complete gradient coverage failed')
    if mode=='grouped_raw' and model.training_state()!=reference.training_state():raise AssertionError('Controlled baseline differs from original cached learner')
    path=Path(output)/(f'seed{seed}-{mode}.training_state.json.gz');save_gzip(path,model.training_state())
    restored=SpongeDQN.from_training_state(json.loads(gzip.decompress(path.read_bytes())))
    if restored.training_state()!=model.training_state():raise AssertionError('Complete taught state did not recover')
    receipt=dict(name=f'seed{seed}-{mode}',seed=seed,mode=mode,cutoff=cutoff,frames=model.frames,updates=model.updates,sampled_source_rows=len(sampled),source_order_sha256=order_hash.hexdigest(),weights_sha256=model.weights_digest(),weights_changed=model.weights_digest()!=initial,state_file=path.name,state_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),full_state_recovered=True,seconds=time.perf_counter()-started,baseline_state_exact=mode=='grouped_raw')
    return model,receipt,dict(before=before,after=diagnostic(model,lessons,indices),curve=curve)

def run(history,corpus,core,output):
    output,corpus=Path(output),Path(corpus)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve earlier studies')
    output.mkdir(parents=True);parent=json.loads((corpus/'protocol.json').read_text());cutoff=int(utc_seconds('2025-04-01T00:00:00Z'));stop=int(utc_seconds('2025-10-01T00:00:00Z'));end=int(utc_seconds(parent['history_end']))
    if subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()!=parent['core']:raise ValueError('Changed actual Core')
    protocol=dict(schema='aiscend.bean.retained_teaching.v1',parent_source='526d57a1927b627082c301fffc274e6d64346ff7',core=parent['core'],seeds=[7,41],cutoff=cutoff,selection_end=stop,audit_end=end,label_availability_guard_days=7,training_arms=['grouped_raw','shuffled_raw','paired_advantage'],controls='Same original examples, input identities, seed, architecture, costs and gradient budget. Shuffled raw and advantage arms share one paired lesson order per seed. Grouped state must equal the original cached implementation.',objective='Immediate raw log wealth versus log wealth advantage of long over cash from the identical account/path; shared market movement and cost effects reconcile exactly.',memory='Fixed unbiased reservoir of at most128 cases per visible exposure/trend/volatility/order-budget regime; at most2048 active cases. Existing PatternMemory recalls32 nearest mature cases excluding query asset in both feature and label sources. When at least16 and squared distance<=1, blend25% mean advantage with75% neural action advantage. Retain memory-only and label-shuffled placebo controls.',diagnostics='Fixed sentinel panels from four source-order quartiles, before and after each training quarter. They are repeated training diagnostics, not new unseen cases.',selection='Compact SQL signed incremental net return over fixed price_trend on April-September2025 training assets; tie lower drawdown then method. Freeze before later audit. Representative seed41 arms audited regardless of winner. Held-out asset identities never enter gradients or selection.',scope='Previously inspected current-survivor historical development data, not independent forward proof',case_codes='Two-character page references to original immutable paired lessons; full sources and failures retained',live_orders=False)
    protocol['protocol_sha256']=digest(protocol);(output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    lessons,source=load_lessons(corpus,cutoff);save_lessons(output/'matched_lessons.jsonl.gz',lessons)
    references,reference_stats,reference_receipts=learn_cached(corpus,core,output/'reference',cutoff,protocol['seeds'],gammas=(0.,))
    if reference_stats['source_sha256']!=source['source_sha256'] or reference_stats['source_rows']!=source['source_rows']:raise AssertionError('Teaching cases differ from original baseline')
    (output/'reference'/'retention_manifest.json').write_text(json.dumps(dict(training=reference_stats,policies=reference_receipts),indent=2)+'\n')
    models={};retained=[];diagnostics={}
    for seed in protocol['seeds']:
        for mode in protocol['training_arms']:
            model,receipt,stats=train_order(lessons,references[f'seed{seed}_gamma0.0'],mode,output,cutoff);models[receipt['name']]=model;retained.append(receipt);diagnostics[receipt['name']]=stats
            print(canonical(dict(event='retained_training_complete',name=receipt['name'],updates=model.updates,before_mse=stats['before']['matched_advantage_mse'],after_mse=stats['after']['matched_advantage_mse'])),flush=True)
    (output/'retention_manifest.json').write_text(json.dumps(dict(source=source,policies=retained,diagnostics=diagnostics),indent=2)+'\n')
    state=active_memory(lessons,cutoff);save_gzip(output/'active_memory.json.gz',state);restored=json.loads(gzip.decompress((output/'active_memory.json.gz').read_bytes()));memory=RetainedMemory(state);restored_memory=RetainedMemory(restored);placebo=RetainedMemory(state,placebo=True)
    if memory.sha256!=restored_memory.sha256:raise AssertionError('Retained lesson dictionary failed recovery')
    for entry in state['entries'][:32]:
        if memory.query(entry['vector'],'RECOVERY-CHECK',cutoff)!=restored_memory.query(entry['vector'],'RECOVERY-CHECK',cutoff):raise AssertionError('Recovered memory recall differs')
    (output/'lesson_legend.json').write_text(json.dumps(dict(memory_sha256=memory.sha256,codes={x['code']:{k:x[k] for k in ('id','source_ids','source_file','source_file_sha256','symbol','outcome_at','reason','rewards','effect_delta')} for x in state['entries']},reason_legend=state['reason_legend']),indent=2)+'\n')
    policies=dict(models)
    for seed in protocol['seeds']:
        model=models[f'seed{seed}-paired_advantage'];policies[f'seed{seed}-paired_plus_memory']=RecallPolicy(model,memory,cutoff);policies[f'seed{seed}-paired_plus_placebo']=RecallPolicy(model,placebo,cutoff)
    policies['memory_only']=RecallPolicy(None,memory,cutoff)
    from bean.optimization.sponge_dqn import SpongeDQN
    blob=(corpus/'untrained.training_state.json.gz').read_bytes();manifest=json.loads((corpus/'artifacts_manifest.json').read_text())['files']
    if hashlib.sha256(blob).hexdigest()!=manifest['untrained.training_state.json.gz']['sha256']:raise ValueError('Changed untrained control')
    untrained=SpongeDQN.from_training_state(json.loads(gzip.decompress(blob)))
    if untrained.frames or untrained.updates:raise ValueError('Control is trained')
    untrained.knowledge_cutoff=cutoff;policies['untrained']=untrained
    catalog,history_manifest,metadata,features=load_snapshot(history);usd=sorted(p for p in features if p.endswith('-USD'));known=[p for p in usd if not withheld(p)];unseen=[p for p in usd if withheld(p)]
    scores=CompactScores(output/'compact_scores');receipts=[]
    def phase(label,products,start,end,name):
        ledger=DecisionLedger(output/'ledgers'/(label+'.sqlite3'));ledger.metadata('scope',dict(label=label,products=products,start=start,end=end,protocol_sha256=protocol['protocol_sha256']))
        result=evaluate(features,metadata,products,start,end,name,ledger,model=policies.get(name),reference='price_trend');path=ledger.path;receipt=dict(path=str(path.with_suffix('.jsonl').relative_to(output))+'.gz',records=ledger.seq,tip=ledger.tip);ledger.close()
        # The original full event bytes are authoritative. Do not duplicate
        # every detailed trace as a compressed derived SQLite snapshot too.
        stream=path.with_suffix('.jsonl')
        with stream.open('rb') as src,gzip.GzipFile(filename=str(stream)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
        path.unlink();stream.unlink();receipt['sha256']=hashlib.sha256(Path(str(output/receipt['path'])).read_bytes()).hexdigest();receipts.append(receipt);return result,receipt
    selection={};selection_receipts={}
    for name in ['cash','buy_hold','price_trend',*policies]:selection[name],selection_receipts[name]=phase('selection-'+name,known,cutoff,stop,name)
    window=dict(phase='internal_training_selection',start=cutoff,end=stop,assets='known')
    for name,row in selection.items():
        keys=dict(method=name,dataset=catalog['catalog_hash'],window=window,seed=int(name[4:name.index('-')]) if name.startswith('seed') else None,cost=.01,configuration=protocol['protocol_sha256'],evidence=selection_receipts[name])
        scores.record(keys=keys,candidate=row,baseline=selection['price_trend'],role='training',available_at=stop,source=selection_receipts[name])
    ranked=scores.rank(int(time.time()),window=window,limit=100)
    if len(ranked)!=len(selection):raise AssertionError('Compact ledger lost a candidate')
    selected=ranked[0]['method'];lock=dict(selected=selected,selection_source='CompactScores.rank',score_mnemonic=ranked[0]['mnemonic'],score_receipt=ranked[0]['receipt'],later_audit_opened=False,withheld_assets_used=False,weights_sha256=policies[selected].weights_digest() if selected in policies else None,selection_returns={n:r['net_return'] for n,r in selection.items()});(output/'selection_lock.json').write_text(json.dumps(lock,indent=2)+'\n')
    audits=[]
    representatives=list(dict.fromkeys(['cash','buy_hold','price_trend','untrained','memory_only','seed41-grouped_raw','seed41-shuffled_raw','seed41-paired_advantage','seed41-paired_plus_memory','seed41-paired_plus_placebo',selected]))
    for cohort,products in (('all_usd',usd),('withheld_assets',unseen)):
        results={};sources={}
        for name in representatives:results[name],sources[name]=phase('audit-'+cohort+'-'+name,products,stop,end,name)
        for name,row in results.items():
            keys=dict(method=name,dataset=catalog['catalog_hash'],window=dict(phase='later_historical_audit',cohort=cohort,start=stop,end=end),seed=41 if name.startswith('seed41') else None,cost=.01,configuration=protocol['protocol_sha256'],evidence=sources[name])
            scores.record(keys=keys,candidate=row,baseline=results['price_trend'],role='evaluation',available_at=end,source=sources[name])
        audits.append(dict(cohort=cohort,results=results));(output/'progress.json').write_text(json.dumps(audits,indent=2)+'\n')
    scores.close();report=dict(schema='aiscend.bean.retained_teaching_results.v1',protocol=protocol,source=source,catalog_markets=len(usd),withheld_assets=len(unseen),active_memory_cases=len(state['entries']),memory_sha256=memory.sha256,memory_recovered=True,diagnostics=diagnostics,selection=lock,selection_results=selection,audits=audits,phase_receipts=receipts,independent_profitability_proven=False,general_causal_understanding_proven=False,live_orders=False)
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Retained BEAN teaching study\n','Historical development only. Same-input controls isolate lesson order and matched advantage. Memory comparisons reuse identical learned weights; label-shuffled recall is retained as a placebo. All detailed decision events and source case codes are preserved. Training probes measure fitting/retention, not independent predictive skill.\n',f"Source rows: {source['source_rows']:,}; matched lessons: {source['pairs']:,}; active memory cases: {len(state['entries']):,}; new-to-training asset identities: {len(unseen)}. Every portfolio is a separate hypothetical $1,000 allocation, not additional bankroll.\n",'| Policy | Internal selection | Later all-market audit | Later withheld-asset audit |','| --- | ---: | ---: | ---: |']
    for name in selection:
        values=[selection[name]]+[x['results'].get(name) for x in audits];lines.append('| '+name+' | '+' | '.join(f"{x['net_return']:+.2%}" if x is not None else 'not preregistered for audit' for x in values)+' |')
    lines+=['\nThe compact ledger selected **'+selected+'** before the later audit. Unchanged-random, fixed-rule, memory-only or cash profits are not neural learning profits. Positive and negative cases and all trained states remain. No live orders or automatic promotion were enabled.\n','## Training retention diagnostics\n','| Model | Before matched-advantage MSE | After |','| --- | ---: | ---: |']
    for name,stats in diagnostics.items():lines.append(f"| {name} | {stats['before']['matched_advantage_mse']:.6f} | {stats['after']['matched_advantage_mse']:.6f} |")
    (output/'RESULTS.md').write_text('\n'.join(lines)+'\n');files={str(p.relative_to(output)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in output.rglob('*') if p.is_file()};(output/'artifacts_manifest.json').write_text(json.dumps(dict(files=files),indent=2)+'\n');print(canonical(dict(event='retained_teaching_complete',selected=selected,source_rows=source['source_rows'],memory_cases=len(state['entries']),audit_returns={x['cohort']:x['results'][selected]['net_return'] for x in audits})),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--history',type=Path,required=True);p.add_argument('--corpus',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.history,a.corpus,a.core_dir,a.output)
