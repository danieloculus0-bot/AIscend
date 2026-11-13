"""BEAN learner consuming auditable, matured strategy explanations and comparisons."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import sys

def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)

def validate_lesson(lesson,cutoff):
    if not lesson['decision_at']<lesson['outcome_at']<=cutoff:raise ValueError('Future or unmatured mentor lesson')
    content={k:v for k,v in lesson.items() if k!='id'}
    if hashlib.sha256(canonical(content).encode()).hexdigest()!=lesson['id']:raise ValueError('Mentor evidence changed')
    hold=lesson['cases'][1]
    for c in lesson['cases']:
        net=c['market_pnl_before_fill']+c['market_pnl_after_fill']-c['paid_cost']
        if not math.isclose(net,c['net_return'],abs_tol=1e-10,rel_tol=1e-10):raise ValueError('Profit explanation does not reconcile')
        expected=100*math.log((1+c['net_return'])/(1+hold['net_return']))
        if not math.isclose(expected,c['advantage_over_hold'],abs_tol=1e-10,rel_tol=1e-10):raise ValueError('Mentor comparison does not reconcile')

def main():
    learner=None;journal=None;cutoff=None;seen=set();tip='0'*64
    for line in sys.stdin:
        try:
            r=json.loads(line);op=r['op']
            if op=='init':
                sys.path.insert(0,str(Path(r['core_dir']).resolve()))
                import torch
                torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
                from bean.optimization.sponge_dqn import SpongeDQN
                from bean.optimization.autodev import Journal
                learner=SpongeDQN(seed=r['seed'],observation_size=r['size'],actions=tuple(r['actions']),domain='aiscend.mentor_comparisons.v1',gamma=0.,lr=.0005,batch_size=64,replay_capacity=4096,warmup=64,learn_every=1000000,target_sync=100)
                journal=Journal(r['journal']);cutoff=r['knowledge_cutoff'];mode=r['mode'];out={'module':type(learner).__module__,'feedback':mode}
            elif op=='teach':
                lessons=r['lessons']
                if len({x['id'] for x in lessons})!=len(lessons):raise ValueError('Duplicated mentor evidence')
                for lesson in lessons:
                    validate_lesson(lesson,cutoff)
                    if [x['action'] for x in lesson['cases']]!=list(learner.actions):raise ValueError('Mentor action ordering differs')
                    if lesson['id'] in seen:raise ValueError('Duplicated mentor evidence')
                receipts=[]
                for lesson in lessons:
                    for action,c in enumerate(lesson['cases']):
                        reward=c['advantage_over_hold'] if mode=='coached_advantage' else c['raw_log_reward']
                        learner.observe(lesson['vector'],action,reward,None,True)
                    seen.add(lesson['id']);tip=hashlib.sha256((tip+'\n'+lesson['id']).encode()).hexdigest()
                    winner=max(lesson['cases'],key=lambda x:x['net_return'])
                    receipts.append({'lesson_id':lesson['id'],'after_maturity':lesson['outcome_at'],'best_hindsight_teacher':winner['action'],'why_teacher_outperformed_hold':{'market_exposure_advantage':winner['market_pnl_before_fill']+winner['market_pnl_after_fill']-lesson['cases'][1]['market_pnl_before_fill']-lesson['cases'][1]['market_pnl_after_fill'],'additional_cost':winner['paid_cost']-lesson['cases'][1]['paid_cost'],'net_advantage':winner['net_return']-lesson['cases'][1]['net_return']},'used_rewards':[c['advantage_over_hold'] if mode=='coached_advantage' else c['raw_log_reward'] for c in lesson['cases']]})
                out={'lessons':len(seen),'frames':learner.frames,'receipt_chain':tip,'receipts':receipts}
            elif op=='train':
                if len(learner.replay)<64 or not learner.updates<=r['updates']<=100000:raise ValueError('Invalid training budget')
                while learner.updates<r['updates']:learner.train_batch()
                out={'updates':learner.updates,'last_loss':learner.last_loss}
            elif op=='values':
                if r['decision_at']<cutoff:raise ValueError('Future learned state')
                for x in r.get('mentor_evidence',[]):
                    if x['outcome_at']>min(cutoff,r['decision_at']):raise ValueError('Future mentor explanation')
                with torch.no_grad():values=learner.online(torch.tensor([learner.encode(v) for v in r['vectors']],dtype=torch.float32)).tolist()
                out={'values':values,'explanation':{'feedback_objective':mode,'expected_strategy_values':values[0],'past_comparison_ids':[x['id'] for x in r.get('mentor_evidence',[])],'past_best_teacher_mechanisms':[x['best_teacher'] for x in r.get('mentor_evidence',[])],'latest_supporting_outcome':max([x['outcome_at'] for x in r.get('mentor_evidence',[])],default=None),'scope':'Predicted values plus measured historical accounting comparisons; not proof of a causal market law'}}
            elif op=='status':out={'updates':learner.updates,'frames':learner.frames,'replay':len(learner.replay),'weights_sha256':learner.weights_digest(),'knowledge_cutoff':cutoff,'mentor_lessons':len(seen),'mentor_receipt_chain':tip}
            elif op=='checkpoint':
                journal.append({'objective':'aiscend.mentor.inputs.v1','iteration':0,'receipt_chain':tip,'mentor_lessons':len(seen),'mode':mode,'knowledge_cutoff':cutoff})
                out=learner.checkpoint(journal,baseline=0.,candidate=r['score'],samples=r['samples'],evaluation_sha256=r['evaluation_sha256'],episode=mode,metadata={'knowledge_cutoff':cutoff,'protocol_sha256':r['protocol_sha256'],'economic_qualification':'DEVELOPMENT_ONLY','mentor_receipt_chain':tip})
            elif op=='close':journal.close();print(json.dumps({'ok':True,'result':{'closed':True}}),flush=True);return
            else:raise ValueError('Unknown mentor operation')
            print(json.dumps({'ok':True,'result':out},allow_nan=False),flush=True)
        except Exception as e:print(json.dumps({'ok':False,'error':type(e).__name__+': '+str(e)}),flush=True)

if __name__=='__main__':main()
