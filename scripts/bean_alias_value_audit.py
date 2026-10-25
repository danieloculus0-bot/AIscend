"""Compare the chunk-trained Core with the same weights and tied action aliases."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from src.bean_strategy_curriculum import Features,load_candles
from src.bean_economic_experiment import evaluate
from src.bean_causal_simulation import DecisionLedger
from src.strategy_atlas import canonical,digest,utc_seconds

class Worker:
    def __init__(self,core,source,entry,mode):
        env={k:v for k,v in os.environ.items() if k in ('PATH','PYTHONPATH','LD_LIBRARY_PATH','LANG')}
        self.p=subprocess.Popen([sys.executable,'-m','src.bean_frozen_action_worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        self.call('init',core_dir=str(Path(core).resolve()),state=str(Path(source)/entry['state_file']),state_sha256=entry['state_sha256'],weights_sha256=entry['online_weights_sha256'],cutoff=entry['cutoff'],mode=mode)
    def call(self,op,**kw):
        self.p.stdin.write(canonical({'op':op,**kw})+'\n');self.p.stdin.flush();line=self.p.stdout.readline()
        if not line:raise RuntimeError(self.p.stderr.read()[-3000:])
        r=json.loads(line)
        if not r['ok']:raise RuntimeError(r['error'])
        return r['result']
    def close(self):
        if self.p.poll() is None:self.call('close');self.p.wait(timeout=15)
        for x in (self.p.stdin,self.p.stdout,self.p.stderr):x.close()

def run(source,candles,core,output):
    source=Path(source);output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve earlier alias audit')
    output.mkdir(parents=True);report=json.loads((source/'results.json').read_text());manifest=json.loads((source/'retention_manifest.json').read_text());features=Features(load_candles(candles));protocol=json.loads((source/'protocol.json').read_text())
    if features.c.source_hash!=protocol['source_sha256']:raise ValueError('Different historical observations')
    audit={'schema':'aiscend.bean.action_alias_audit.v1','source_protocol_sha256':protocol['protocol_sha256'],'scope':'SEEN_HISTORY_SAME_WEIGHTS_NO_ADDITIONAL_TRAINING','implementation':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ('src/bean_action_aliases.py','src/bean_frozen_action_worker.py','scripts/bean_alias_value_audit.py')},'method':'Average raw values within exact identical-target groups; exact holding cash value is zero. Holding an intermediate exposure remains separate from rebalancing to that exposure.','promotion':'No automatic promotion or claim of independent profitability','live_orders':False};audit['protocol_sha256']=digest(audit);ledger=DecisionLedger(output/'decisions.sqlite3');ledger.metadata('protocol',audit);results=[]
    for row in report['results']:
        fold=row['fold'];entry=next(x for x in manifest['policies'] if x['id']==f'fold-{fold}-raw_chunked');pair={}
        for mode in ('original','consistent_aliases'):
            worker=Worker(core,source,entry,mode)
            try:pair[mode]=evaluate(features,int(utc_seconds(row['start'])),int(utc_seconds(row['end'])),ledger,f'fold-{fold}/'+mode,worker=worker)
            finally:worker.close()
        if abs(pair['original']['ending_equity']-row['models']['raw_chunked']['result']['ending_equity'])>1e-6:raise ValueError('Original chunk-trained policy does not reproduce')
        results.append({'fold':fold,'year':row['start'][:4],'source_weights_sha256':entry['online_weights_sha256'],'original_reproduced':True,'results':pair,'alias_effect_on_net_return':pair['consistent_aliases']['net_return']-pair['original']['net_return']})
    outcome={'protocol':audit,'results':results,'new_training_updates':0,'independent_profitability_proven':False,'decision_records':ledger.seq,'decision_ledger_tip':ledger.tip,'live_orders':False};ledger.metadata('results',outcome);ledger.close();(output/'results.json').write_text(json.dumps(outcome,indent=2)+'\n');(output/'protocol.json').write_text(json.dumps(audit,indent=2)+'\n')
    for p in list(output.iterdir()):
        if p.suffix in ('.sqlite3','.jsonl'):
            with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    lines=['# Same-weight action-alias audit\n','Actual chunk-trained Core checkpoints are recovered independently. No new neural updates occur. The original financial path must first reproduce within 1e-6. This remains already-seen development history on eight instruments.\n','| Window | Original chunk-trained return | Consistent-alias return | Difference |','| --- | ---: | ---: | ---: |']
    for r in results:lines.append(f"| {r['year']} | {r['results']['original']['net_return']:+.2%} | {r['results']['consistent_aliases']['net_return']:+.2%} | {r['alias_effect_on_net_return']:+.2%} |")
    lines.append('\nThis isolates an inference convention with the same learned weights, fixed cost model and capital. It does not establish useful market forecasting, a future edge, or superiority to other traders. All gains and losses remain included.\n');(output/'RESULTS.md').write_text('\n'.join(lines));print(canonical({'event':'action_alias_audit_complete','effects':[r['alias_effect_on_net_return'] for r in results]}),flush=True);return outcome

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--candles',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.source,a.candles,a.core_dir,a.output)
