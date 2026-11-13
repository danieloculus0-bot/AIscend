"""Recover any retained curriculum checkpoint by its actual state hash and cutoff."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
from src.strategy_atlas import utc_seconds

def compare_predictions(actual,expected,*,rtol=1e-5,atol=1e-6):
    """Float32 CPU kernels may round differently; weights remain SHA-exact.

    Require every value within tolerance AND the same greedy action. This is
    solely a portable recovery check, not a relaxation of economic scoring.
    """
    a,b=actual['values'],expected['values']
    if len(a)!=len(b) or any(len(x)!=len(y) or not x for x,y in zip(a,b)):raise ValueError('Recovered prediction shape differs')
    error=max((abs(x-y) for row_a,row_b in zip(a,b) for x,y in zip(row_a,row_b)),default=0.)
    choices_equal=all(max(range(len(x)),key=x.__getitem__)==max(range(len(y)),key=y.__getitem__) for x,y in zip(a,b))
    values_close=all(math.isfinite(x) and math.isfinite(y) and math.isclose(x,y,rel_tol=rtol,abs_tol=atol) for row_a,row_b in zip(a,b) for x,y in zip(row_a,row_b))
    if not values_close or not choices_equal:raise ValueError(f'Recovered diagnostic differs: max_abs_error={error}, same_greedy_action={choices_equal}')
    return {'max_absolute_error':error,'same_greedy_action':choices_equal,'relative_tolerance':rtol,'absolute_tolerance':atol,'values_exactly_equal':a==b}

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
            objective=item.get('objective',OBJECTIVE)
            if objective not in (OBJECTIVE,'aiscend.mentor.unscored_training.v1'):raise ValueError('Unsupported retained learning objective')
            rows=[x for x in journal.events(objective) if x['online_weights_sha256']==item['state']['weights_sha256'] and x['updates']==item['state']['updates']]
            if not rows:raise ValueError('Checkpoint is absent from retained SHA Journal')
            row=rows[-1]
            if row['metadata']['knowledge_cutoff']!=int(utc_seconds(item['knowledge_cutoff'])):raise ValueError('Retained knowledge cutoff changed')
            model=SpongeDQN.from_training_state(unpack(row['training_state']))
            if model.weights_digest()!=item['state']['weights_sha256']:raise ValueError('Actual learned weights differ')
            values=None;comparison=None
            if item.get('diagnostic_vector'):
                with torch.no_grad():values={'values':model.online(torch.tensor([model.encode(item['diagnostic_vector'])],dtype=torch.float32)).tolist()}
                comparison=compare_predictions(values,item['diagnostic_values'])
            return {'policy_id':policy_id,'module':type(model).__module__,'knowledge_cutoff':item['knowledge_cutoff'],'updates':model.updates,'frames':model.frames,'replay_entries':len(model.replay),'weights_sha256':model.weights_digest(),'optimizer_recovered':bool(model.optimizer.state),'diagnostic_values_match':True if comparison is not None else None,'prediction_recovery':comparison,'economic_qualification':item['economic_qualification'],'live_orders':False}
        finally:journal.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);p.add_argument('--policy-id',default='selected');p.add_argument('--as-of',required=True);a=p.parse_args()
    print(json.dumps(verify(a.directory,a.core_dir,a.policy_id,a.as_of),indent=2))

if __name__=='__main__':main()
