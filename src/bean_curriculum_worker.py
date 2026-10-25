"""Actual BEAN replay learner, isolated from historical files and future labels."""
from __future__ import annotations
import json
from pathlib import Path
import sys

def main():
    learner=None; journal=None; cutoff=None
    for line in sys.stdin:
        try:
            r=json.loads(line);op=r['op']
            if op=='init':
                sys.path.insert(0,str(Path(r['core_dir']).resolve()))
                import torch
                torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
                from bean.optimization.sponge_dqn import SpongeDQN
                from bean.optimization.sponge_dqn import OBJECTIVE
                from bean.optimization.autodev import Journal
                journal=Journal(r['journal']);cutoff=r['knowledge_cutoff']
                learner=SpongeDQN.recover_latest(journal) if r.get('restore') else SpongeDQN(seed=r['seed'],observation_size=r['size'],actions=tuple(r['actions']),domain='aiscend.candle_curriculum.v1',gamma=0.,lr=.0005,batch_size=64,replay_capacity=4096,warmup=64,learn_every=1000000,target_sync=100)
                if r.get('restore') and journal.events(OBJECTIVE)[-1]['metadata']['knowledge_cutoff']!=cutoff:raise ValueError('Requested cutoff differs from retained knowledge')
                out={'module':type(learner).__module__,'gamma':learner.gamma}
            elif op=='teach':
                for x in r['experiences']:
                    if not x['decision_at']<x['outcome_at']<=cutoff:raise ValueError('Unmatured or future teaching label')
                for x in r['experiences']:
                    learner.observe(x['vector'],x['action'],x['reward'],None,True)
                out={'replay':len(learner.replay),'frames':learner.frames}
            elif op=='train':
                budget=r['updates']
                if type(budget) is not int or not learner.updates<=budget<=100000:raise ValueError('Invalid absolute update budget')
                if len(learner.replay)<learner.batch_size:raise ValueError('Insufficient mature teaching examples')
                while learner.updates<budget:learner.train_batch()
                out={'updates':learner.updates,'loss':learner.last_loss}
            elif op=='values':
                if r['decision_at']<cutoff:raise ValueError('Policy contains later knowledge')
                with torch.no_grad():out={'values':learner.online(torch.tensor([learner.encode(x) for x in r['vectors']],dtype=torch.float32)).tolist()}
            elif op=='status':out={'updates':learner.updates,'frames':learner.frames,'replay':len(learner.replay),'weights_sha256':learner.weights_digest(),'knowledge_cutoff':cutoff}
            elif op=='checkpoint':
                out=learner.checkpoint(journal,baseline=0.,candidate=r['score'],samples=r['samples'],evaluation_sha256=r['evaluation_sha256'],episode=r['episode'],metadata={'knowledge_cutoff':cutoff,'economic_qualification':'DEVELOPMENT_ONLY','protocol_sha256':r['protocol_sha256']})
            elif op=='close':
                journal.close();print(json.dumps({'ok':True,'result':{'closed':True}}),flush=True);return
            else:raise ValueError('Unknown curriculum operation')
            print(json.dumps({'ok':True,'result':out},allow_nan=False),flush=True)
        except Exception as e:print(json.dumps({'ok':False,'error':type(e).__name__+': '+str(e)}),flush=True)

if __name__=='__main__':main()
