"""Train on withdrawable equity changes using the existing chunk scheduler.

The preceding alias audit supplies the same-architecture, same-budget control.
Changing observed labels is a development hypothesis, never new market evidence.
"""
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
from .bean_strategy_curriculum import ACTIONS,Features,load_candles
from .bean_economic_values import funded_values
from .bean_route_values import liquidation_values
from .bean_action_aliases import consistent_values
from .bean_economic_experiment import Worker,ChunkAdapter,evaluate,recover
from .bean_training_batches import digest_chunks
from .bean_causal_simulation import DecisionLedger
from .strategy_atlas import canonical,digest,utc_seconds

def relabel(raw,market,symbols):
    """Join exact per-case receipts; reject missing cases or unreconciled rewards."""
    raw_by={x['id']:x for x in raw};market_by={x['id']:x for x in market}
    if len(raw_by)!=len(raw) or len(market_by)!=len(market):raise ValueError('Repeated source evidence')
    changed={}
    for t in sorted({x['decision_at'] for x in raw}):
        for s in symbols:
            if digest([s,t,0.,0]) not in raw_by:continue
            outcomes=[market_by[digest([s,t,'market',a])] for a in (0,1)]
            if any(x['outcome_at']!=outcomes[0]['outcome_at'] for x in outcomes):raise ValueError('Mismatched market outcome times')
            actual=[x['reward'] for x in outcomes]
            for w in (0.,.5,1.):
                group=[raw_by[digest([s,t,w,a])] for a in range(len(ACTIONS))]
                if any(x['vector']!=group[0]['vector'] or x['outcome_at']!=outcomes[0]['outcome_at'] for x in group):raise ValueError('Mismatched starting state')
                old=funded_values(group[0]['vector'],actual);new=liquidation_values(group[0]['vector'],actual)
                for a,x in enumerate(group):
                    if not math.isclose(old[a]['net_log_return'],x['reward'],abs_tol=1e-8,rel_tol=1e-8):raise ValueError('Source reward does not reconcile to source market path')
                    changed[x['id']]={**x,'id':digest([x['id'],'net_liquidation']),'source_id':x['id'],'old_reward':x['reward'],'reward':new[a]['net_log_return'],'paid_cost':new[a]['paid_cost'],'outstanding_exit_cost':new[a]['pending_exit_cost']}
    if len(changed)!=len(raw):raise ValueError('Missing individual source cases; refuse to invent training evidence')
    return [changed[x['id']] for x in raw]

class AliasAdapter:
    def __init__(self,worker):self.worker=worker
    def call(self,op,**kw):
        r=self.worker.call(op,**kw)
        if op=='values':return {**r,'values':[consistent_values(v,q) for v,q in zip(kw['vectors'],r['values'])]}
        return r

def run(source,control,candles,core,output):
    source,control,output=map(Path,(source,control,output))
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve prior liquidation-learning experiment')
    output.mkdir(parents=True);base=json.loads((source/'results.json').read_text());base_protocol=json.loads((source/'protocol.json').read_text());alias=json.loads((control/'results.json').read_text());features=Features(load_candles(candles))
    if alias['protocol']['source_protocol_sha256']!=base_protocol['protocol_sha256'] or features.c.source_hash!=base_protocol['source_sha256']:raise ValueError('Unmatched controls or historical source')
    for name,sha in base_protocol['implementation'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=sha:raise ValueError('Underlying experiment implementation differs from matched control')
    if subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()!=base_protocol['core_revision']:raise ValueError('Different Core implementation')
    artifacts=json.loads((source/'artifacts_manifest.json').read_text())['files']
    records=[]
    for name in ('raw_examples.jsonl.gz','distinct_market_examples.jsonl.gz'):
        blob=(source/name).read_bytes()
        if hashlib.sha256(blob).hexdigest()!=artifacts[name]['sha256']:raise ValueError('Training source changed')
        records.append([json.loads(x) for x in gzip.decompress(blob).splitlines()])
    data=relabel(records[0],records[1],features.c.symbols)
    protocol={'schema':'aiscend.bean.net_liquidation_learning.v1','scope':'ALREADY_SEEN_DEVELOPMENT_HISTORY','source_protocol_sha256':base_protocol['protocol_sha256'],'control_protocol_sha256':alias['protocol']['protocol_sha256'],'core_revision':base_protocol['core_revision'],'implementation':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ('src/bean_net_liquidation_learning.py','src/bean_route_values.py','src/bean_action_aliases.py','src/bean_training_batches.py')},'objective':'100 log of future net-liquidation equity divided by current net-liquidation equity. Current orders and outstanding future exit cost are reconciled; unchanged holding does not pay a new exit fee every period.','matched_conditions':'Same 47 known inputs, 11 actions, seed41, 1024 updates, 4096 replay, digest_chunks order, alias-consistent inference, $1000 allocation, source periods and modeled costs. Only supervised reward basis changes relative to the frozen alias-control checkpoints.','promotion':'No automatic activation; later independent evaluation required','live_orders':False};protocol['protocol_sha256']=digest(protocol);(output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');ledger=DecisionLedger(output/'decisions.sqlite3');ledger.metadata('protocol',protocol)
    with gzip.GzipFile(filename=str(output/'reconciled_training_cases.jsonl.gz'),mode='wb',mtime=0) as dest:
        for x in data:dest.write((canonical(x)+'\n').encode())
    rows=[];retained=[]
    for b in base['results']:
        fold=b['fold'];cutoff=int(utc_seconds(b['start']));end=int(utc_seconds(b['end']));cohort=[x for x in data if x['outcome_at']<=cutoff];order=np.random.default_rng(41).permutation(len(cohort));worker=Worker(core,output/f'fold-{fold}-net-liquidation.sqlite3','net_liquidation',cutoff,4096)
        try:
            initial=worker.call('status');digestion=digest_chunks(ChunkAdapter(worker),[cohort[n] for n in order],field='experiences',updates=1024,batch_size=256,transitions_per_item=1)
            coverage=worker.call('train',updates=1024);coverage['digestion']=digestion
            result=evaluate(features,cutoff,end,ledger,f'fold-{fold}/net_liquidation_learning',worker=AliasAdapter(worker))
            checkpoint=worker.call('checkpoint',coverage=coverage,protocol_sha256=protocol['protocol_sha256']);checkpoint['id']=f'fold-{fold}-net-liquidation';checkpoint['recovery']=recover(output,core,checkpoint);checkpoint['weights_changed']=initial['weights_sha256']!=checkpoint['online_weights_sha256'];retained.append(checkpoint)
            reference=next(x for x in alias['results'] if x['fold']==fold)['results']['consistent_aliases'];rows.append({'fold':fold,'year':b['start'][:4],'control_alias_consistent_gross_reward':reference,'net_liquidation_reward':result,'reward_change_effect_on_net_return':result['net_return']-reference['net_return'],'coverage':coverage})
        finally:worker.close()
    report={'protocol':protocol,'results':rows,'training_cases_reconciled':len(data),'full_states_recovered':len(retained),'independent_profitability_proven':False,'best_on_market_proven':False,'decision_records':ledger.seq,'decision_ledger_tip':ledger.tip,'live_orders':False};ledger.metadata('results',report);ledger.close();(output/'results.json').write_text(json.dumps(report,indent=2)+'\n');(output/'retention_manifest.json').write_text(json.dumps({'policies':retained},indent=2)+'\n')
    for p in list(output.iterdir()):
        if p.suffix in ('.sqlite3','.jsonl'):
            with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    lines=['# Net-liquidation reward learning\n','Actual BEAN Core is trained on individually reconciled mature evidence. The frozen alias audit supplies the same-budget, alias-consistent old-reward control. These are already inspected development periods, not new independent tests.\n','| Window | Gross-reward control with aliases | Net-liquidation reward with aliases | Difference |','| --- | ---: | ---: | ---: |']
    for r in rows:lines.append(f"| {r['year']} | {r['control_alias_consistent_gross_reward']['net_return']:+.2%} | {r['net_liquidation_reward']['net_return']:+.2%} | {r['reward_change_effect_on_net_return']:+.2%} |")
    lines.append(f"\n{len(data):,} original case rewards reconciled against their exact source market components before relabeling. Three full optimizer/target/RNG/replay states independently recovered. No automatic production promotion or competitor-superiority claim. All outcome records and older controls remain retained.\n");(output/'RESULTS.md').write_text('\n'.join(lines));print(canonical({'event':'net_liquidation_learning_complete','returns':[r['net_liquidation_reward']['net_return'] for r in rows]}),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--control',type=Path,required=True);p.add_argument('--candles',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.source,a.control,a.candles,a.core_dir,a.output)
