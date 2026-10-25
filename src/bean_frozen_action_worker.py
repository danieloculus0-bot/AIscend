"""Recover a fixed actual Core model and apply a registered value convention."""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import sys
from .bean_action_aliases import consistent_values

def main():
    for line in sys.stdin:
        try:
            r=json.loads(line);op=r['op']
            if op=='init':
                sys.path.insert(0,str(Path(r['core_dir']).resolve()))
                import torch
                torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
                from bean.optimization.sponge_dqn import SpongeDQN
                raw=Path(r['state']).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=r['state_sha256']:raise ValueError('Training state file changed')
                model=SpongeDQN.from_training_state(json.loads(gzip.decompress(raw)));cutoff=r['cutoff'];mode=r['mode']
                if model.weights_digest()!=r['weights_sha256']:raise ValueError('Recovered weights differ')
                out={'weights_sha256':model.weights_digest(),'updates':model.updates}
            elif op=='status':out={'weights_sha256':model.weights_digest(),'updates':model.updates,'cutoff':cutoff,'value_convention':mode}
            elif op=='values':
                if r['decision_at']<cutoff:raise ValueError('Future learned weights')
                with torch.no_grad():values=model.online(torch.tensor([model.encode(v) for v in r['vectors']],dtype=torch.float32)).tolist()
                out={'values':[consistent_values(v,q) for v,q in zip(r['vectors'],values)] if mode=='consistent_aliases' else values}
            elif op=='close':print(json.dumps({'ok':True,'result':{'closed':True}}),flush=True);return
            else:raise ValueError('No training is allowed in the frozen value audit')
            print(json.dumps({'ok':True,'result':out},allow_nan=False),flush=True)
        except Exception as e:print(json.dumps({'ok':False,'error':type(e).__name__+': '+str(e)}),flush=True)

if __name__=='__main__':main()
