"""Recover any retained curriculum checkpoint by its actual state hash and cutoff."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from src.strategy_atlas import utc_seconds

def verify(directory,core_dir,policy_id,as_of):
    directory=Path(directory);manifest=json.loads((directory/'retention_manifest.json').read_text())
    item=next(x for x in manifest['policies'] if x['id']==policy_id)
    if utc_seconds(as_of)<utc_seconds(item['knowledge_cutoff']):raise ValueError('Retained policy contains later knowledge')
    raw=(directory/item['file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=item['file_sha256']:raise ValueError('Retained journal bytes changed')
    sys.path.insert(0,str(Path(core_dir).resolve()))
    from bean.optimization.autodev import Journal
    from bean.optimization.sponge_dqn import SpongeDQN,OBJECTIVE
    from bean.optimization.sponge_codec import unpack
    import torch
    torch.set_num_threads(1)
    with tempfile.TemporaryDirectory(prefix='bean-curriculum-recover-') as temp:
        path=Path(temp)/'policy.sqlite3';path.write_bytes(gzip.decompress(raw));journal=Journal(path)
        try:
            rows=[x for x in journal.events(OBJECTIVE) if x['online_weights_sha256']==item['state']['weights_sha256'] and x['updates']==item['state']['updates']]
            if not rows:raise ValueError('Checkpoint is absent from retained SHA Journal')
            row=rows[-1]
            if row['metadata']['knowledge_cutoff']!=int(utc_seconds(item['knowledge_cutoff'])):raise ValueError('Retained knowledge cutoff changed')
            model=SpongeDQN.from_training_state(unpack(row['training_state']))
            if model.weights_digest()!=item['state']['weights_sha256']:raise ValueError('Actual learned weights differ')
            values=None
            if item.get('diagnostic_vector'):
                with torch.no_grad():values={'values':model.online(torch.tensor([model.encode(item['diagnostic_vector'])],dtype=torch.float32)).tolist()}
                if values!=item['diagnostic_values']:raise ValueError('Recovered diagnostic decision values differ')
            return {'policy_id':policy_id,'module':type(model).__module__,'knowledge_cutoff':item['knowledge_cutoff'],'updates':model.updates,'frames':model.frames,'replay_entries':len(model.replay),'weights_sha256':model.weights_digest(),'optimizer_recovered':bool(model.optimizer.state),'diagnostic_values_match':values==item.get('diagnostic_values') if values else None,'economic_qualification':item['economic_qualification'],'live_orders':False}
        finally:journal.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--policy-id',default='selected');p.add_argument('--as-of',required=True);a=p.parse_args()
    print(json.dumps(verify(a.directory,a.core_dir,a.policy_id,a.as_of),indent=2))

if __name__=='__main__':main()
