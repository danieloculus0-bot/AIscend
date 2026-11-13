"""Guarantee a training-batch visit for every mature source row, then compare PnL."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import numpy as np
from .bean_economic_experiment import Worker,ChunkAdapter,evaluate,recover
from .bean_net_liquidation_learning import relabel,AliasAdapter
from .bean_strategy_curriculum import Features,load_candles
from .bean_training_batches import digest_chunks
from .bean_causal_simulation import DecisionLedger
from .strategy_atlas import canonical,digest,utc_seconds

def full_pass_budget(items,updates,batch_size=64):
    if items<batch_size or updates<math.ceil(items/batch_size):raise ValueError('Update budget cannot cover every supplied row')
    return {'items':items,'minimum_batches':math.ceil(items/batch_size),'updates':updates,'batch_size':batch_size}

def run(source,alias_control,liquidation_control,candles,core,output):
    source,alias_control,liquidation_control,output=map(Path,(source,alias_control,liquidation_control,output))
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve prior full-pass experiment')
    output.mkdir(parents=True);base=json.loads((source/'results.json').read_text());bp=json.loads((source/'protocol.json').read_text());aliases=json.loads((alias_control/'results.json').read_text());liquidation=json.loads((liquidation_control/'results.json').read_text());features=Features(load_candles(candles))
    if features.c.source_hash!=bp['source_sha256'] or aliases['protocol']['source_protocol_sha256']!=bp['protocol_sha256'] or liquidation['protocol']['source_protocol_sha256']!=bp['protocol_sha256']:raise ValueError('Unmatched source or controls')
    if subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()!=bp['core_revision']:raise ValueError('Different Core implementation')
    source_manifest=json.loads((source/'artifacts_manifest.json').read_text())['files'];data=[]
    for name in ('raw_examples.jsonl.gz','distinct_market_examples.jsonl.gz'):
        blob=(source/name).read_bytes()
        if hashlib.sha256(blob).hexdigest()!=source_manifest[name]['sha256']:raise ValueError('Changed training evidence')
        data.append([json.loads(x) for x in gzip.decompress(blob).splitlines()])
    raw,market=data;net=relabel(raw,market,features.c.symbols)
    protocol={'schema':'aiscend.bean.full_pass_learning.v1','scope':'ALREADY_SEEN_DEVELOPMENT_HISTORY','source_protocol_sha256':bp['protocol_sha256'],'alias_control_protocol_sha256':aliases['protocol']['protocol_sha256'],'liquidation_control_protocol_sha256':liquidation['protocol']['protocol_sha256'],'core_revision':bp['core_revision'],'implementation':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ('src/bean_full_pass_learning.py','src/bean_economic_worker.py','src/bean_training_batches.py','src/bean_net_liquidation_learning.py','src/bean_action_aliases.py')},'schedule':'64-row replay equals the 64-row gradient batch. digest_chunks alternates each <=64-row cohort with gradients. Every current row participates, including the final partial cohort, before it can be evicted. Remaining allocated updates rehearse the current batch. This differs from mixed 4096 replay and is explicitly a schedule/replay-size ablation.','controls':'Same Core architecture, seed41, 1024 updates, ordered source evidence, alias-consistent inference, funded $1000 allocation and costs. Gross and net-liquidation reward variants both retained.','qualification':'No automatic promotion; full row coverage is not proof of learning a profitable edge.','live_orders':False};protocol['protocol_sha256']=digest(protocol);(output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');ledger=DecisionLedger(output/'decisions.sqlite3');ledger.metadata('protocol',protocol);rows=[];retained=[]
    for fold in base['results']:
        year=fold['start'][:4];n=fold['fold'];cutoff=int(utc_seconds(fold['start']));end=int(utc_seconds(fold['end']))
        for mode,all_data in (('gross',raw),('net_liquidation',net)):
            cohort=[x for x in all_data if x['outcome_at']<=cutoff];plan=full_pass_budget(len(cohort),1024);order=np.random.default_rng(41).permutation(len(cohort));worker=Worker(core,output/f'fold-{n}-{mode}.sqlite3','full_pass_'+mode,cutoff,64)
            try:
                initial=worker.call('status');digestion=digest_chunks(ChunkAdapter(worker),[cohort[i] for i in order],field='experiences',updates=1024,batch_size=64,transitions_per_item=1,replay_capacity=64)
                coverage=worker.call('train',updates=1024);coverage['digestion']=digestion
                if coverage['distinct_source_examples_sampled']!=len(cohort) or coverage['discarded_before_training']!=0:raise AssertionError('Source row never reached the gradient batch')
                outcome=evaluate(features,cutoff,end,ledger,f'fold-{n}/full_pass_'+mode,worker=AliasAdapter(worker));entry=worker.call('checkpoint',coverage=coverage,protocol_sha256=protocol['protocol_sha256']);entry['id']=f'fold-{n}-{mode}';entry['recovery']=recover(output,core,entry);entry['weights_changed']=entry['online_weights_sha256']!=initial['weights_sha256'];retained.append(entry)
                control=next(r for r in aliases['results'] if r['fold']==n)['results']['consistent_aliases'] if mode=='gross' else next(r for r in liquidation['results'] if r['fold']==n)['net_liquidation_reward']
                rows.append({'fold':n,'year':year,'reward_basis':mode,'control_4096_replay':control,'full_pass':outcome,'effect_on_net_return':outcome['net_return']-control['net_return'],'plan':plan,'coverage':coverage})
            finally:worker.close()
    report={'protocol':protocol,'results':rows,'all_source_rows_reached_training':True,'full_states_recovered':len(retained),'independent_profitability_proven':False,'best_on_market_proven':False,'decision_records':ledger.seq,'decision_ledger_tip':ledger.tip,'live_orders':False};ledger.metadata('results',report);ledger.close();(output/'results.json').write_text(json.dumps(report,indent=2)+'\n');(output/'retention_manifest.json').write_text(json.dumps({'policies':retained},indent=2)+'\n')
    for p in list(output.iterdir()):
        if p.suffix in ('.sqlite3','.jsonl'):
            with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    lines=['# Full-pass matured-evidence learning\n','Every supplied source row participated in at least one actual Core gradient batch before eviction; source-ID coverage is checked, not inferred from frame counters. This is not proof of semantic understanding or profitability. The replay is deliberately only one mini-batch; older examples remain in the immutable corpus and older learned weights are checkpointed. The effect of the changed rehearsal schedule is measured rather than assumed. All dates remain already inspected development history.\n','| Window | Reward basis | Prior 4096-replay control | Complete-pass schedule | Difference |','| --- | --- | ---: | ---: | ---: |']
    for r in rows:lines.append(f"| {r['year']} | {r['reward_basis']} | {r['control_4096_replay']['net_return']:+.2%} | {r['full_pass']['net_return']:+.2%} | {r['effect_on_net_return']:+.2%} |")
    lines.append('\nSeed, architecture and total updates match the controls, while replay size and gradient timing change together. All six complete Core optimizer/target/RNG/replay states independently recovered. No fallback profit, independent predictive edge or competitor superiority is attributed to passing software/coverage checks.\n');(output/'RESULTS.md').write_text('\n'.join(lines));print(canonical({'event':'full_pass_learning_complete','all_rows_sampled':True,'returns':[{k:r[k] for k in ('year','reward_basis','effect_on_net_return')} for r in rows]}),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--alias-control',type=Path,required=True);p.add_argument('--liquidation-control',type=Path,required=True);p.add_argument('--candles',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.source,a.alias_control,a.liquidation_control,a.candles,a.core_dir,a.output)
