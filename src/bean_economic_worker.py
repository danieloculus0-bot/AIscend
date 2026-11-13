"""Actual BEAN Core learner for replay and economic-value ablations."""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import sys
from .bean_economic_values import funded_values,market_vector

def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)

def main():
    model=None;cutoff=None;scheme=None;journal=None;seen=set();sampled=set();replay_sources={};source_ids=[]
    for line in sys.stdin:
        try:
            r=json.loads(line);op=r['op']
            if op=='init':
                sys.path.insert(0,str(Path(r['core_dir']).resolve()))
                import torch
                torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
                from bean.optimization.sponge_dqn import SpongeDQN
                from bean.optimization.autodev import Journal
                cutoff=r['cutoff'];scheme=r['scheme'];journal_path=Path(r['journal']);journal=Journal(journal_path)
                model=SpongeDQN(seed=41,observation_size=35 if scheme=='factor' else 47,actions=('gap_log_return','holding_log_return') if scheme=='factor' else tuple(r['actions']),domain='aiscend.economic_values.'+scheme,gamma=0.,lr=.0005,batch_size=64,replay_capacity=r['capacity'],warmup=64,learn_every=1000000,target_sync=100)
                out={'module':type(model).__module__,'scheme':scheme,'capacity':r['capacity']}
            elif op=='teach':
                batch=r['examples'];ids=[x['id'] for x in batch]
                if len(set(ids))!=len(ids) or any(x in seen for x in ids):raise ValueError('Repeated teaching example')
                if any(not x['decision_at']<x['outcome_at']<=cutoff for x in batch):raise ValueError('Future or unmatured example')
                for x in batch:
                    if len(model.replay)==model.replay.maxlen:replay_sources.pop(id(model.replay[0]),None)
                    model.observe(x['vector'],x['action'],x['reward'],None,True);seen.add(x['id']);source_ids.append(x['id'])
                    replay_sources[id(model.replay[-1])]=x['id']
                out={'frames':model.frames,'retained':len(model.replay),'updates':model.updates}
            elif op=='train':
                if type(r['updates']) is not int or not model.updates<=r['updates']<=100000 or len(model.replay)<64:raise ValueError('Invalid complete-data training budget')
                original_sample=model.rng.sample
                def counted_sample(population,k):
                    selected=original_sample(population,k)
                    sampled.update(replay_sources[id(x)] for x in selected)
                    return selected
                model.rng.sample=counted_sample
                try:
                    while model.updates<r['updates']:model.train_batch()
                finally:model.rng.sample=original_sample
                out={'updates':model.updates,'last_loss':model.last_loss,'sampled_replay_entries':len(sampled),'distinct_source_examples_sampled':len(sampled),'available_replay_entries':len(model.replay),'discarded_before_training':len(seen-sampled-set(replay_sources.values())),'evicted_total':model.frames-len(model.replay)}
            elif op=='values':
                if r['decision_at']<cutoff:raise ValueError('Policy contains later knowledge')
                vectors=r['vectors'];inputs=[market_vector(v) for v in vectors] if scheme=='factor' else vectors
                for v,a in zip(vectors,r.get('accounts',[])):
                    if abs((1-a['cash']/a['equity'])-v[15])>1e-8:raise ValueError('Visible account and funded exposure differ')
                with torch.no_grad():prediction=model.online(torch.tensor(inputs,dtype=torch.float32)).tolist()
                if scheme=='factor':out={'values':[[x['net_log_return'] for x in funded_values(v,p)] for v,p in zip(vectors,prediction)],'market_forecasts':prediction}
                else:out={'values':prediction}
            elif op=='status':out={'updates':model.updates,'frames':model.frames,'weights_sha256':model.weights_digest(),'replay':len(model.replay),'cutoff':cutoff}
            elif op=='checkpoint':
                # Full-corpus replay can exceed the old codec's 16 MB ceiling.
                # Retain the exact Core training_state externally instead of
                # silently truncating it. Journal hashes bind the complete file.
                state=model.training_state();raw=canonical(state).encode();path=journal_path.with_suffix('.training_state.json.gz')
                with gzip.GzipFile(filename=str(path),mode='wb',mtime=0) as dest:dest.write(raw)
                event={'objective':'aiscend.economic.training.v1','iteration':0,'state_file':path.name,'state_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'state_json_sha256':hashlib.sha256(raw).hexdigest(),'uncompressed_bytes':len(raw),'online_weights_sha256':model.weights_digest(),'updates':model.updates,'frames':model.frames,'cutoff':cutoff,'scheme':scheme,'source_ids_sha256':hashlib.sha256(canonical(source_ids).encode()).hexdigest(),'coverage':r['coverage'],'protocol_sha256':r['protocol_sha256'],'qualification':'DEVELOPMENT_ONLY'}
                tip=journal.append(event);out={**event,'journal_tip':tip,'journal':journal_path.name}
            elif op=='close':journal.close();print(json.dumps({'ok':True,'result':{'closed':True}}),flush=True);return
            else:raise ValueError('Unknown economic learner operation')
            print(json.dumps({'ok':True,'result':out},allow_nan=False),flush=True)
        except Exception as e:print(json.dumps({'ok':False,'error':type(e).__name__+': '+str(e)}),flush=True)

if __name__=='__main__':main()
