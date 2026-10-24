"""Retain learning from the student's actual mistakes after outcomes mature.

This checkpoint is unscored: its source outcomes have already been inspected.
No later financial evaluation is implied by retaining further gradient updates.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import numpy as np
from src.bean_strategy_coach import mentor_lesson
from src.bean_mentor_worker import validate_lesson
from src.bean_strategy_curriculum import ACTIONS,Features,load_candles
from src.strategy_atlas import canonical,digest,utc_seconds

def matched_comparison(lesson,student_action):
    student=next(x for x in lesson['cases'] if x['action']==student_action);teacher=max(lesson['cases'],key=lambda x:x['net_return'])
    gross=lambda x:x['market_pnl_before_fill']+x['market_pnl_after_fill']
    market_advantage=gross(teacher)-gross(student);additional_cost=teacher['paid_cost']-student['paid_cost'];gap=teacher['net_return']-student['net_return']
    if abs(market_advantage-additional_cost-gap)>1e-10:raise ValueError('Student/mentor explanation does not reconcile')
    reasons=[]
    if gross(student)<0:reasons.append('M01_STUDENT_MARKET_LOSS')
    if gross(student)>0 and student['net_return']<=0:reasons.append('F01_COSTS_ERASED_GAIN')
    if additional_cost< -1e-12:reasons.append('F02_MENTOR_PAID_LESS')
    if market_advantage>1e-12:reasons.append('M02_MENTOR_EXPOSURE_GAIN')
    if abs(gap)<1e-12:reasons.append('T01_NO_BETTER_MATCHED_ACTION')
    return {'lesson_id':lesson['id'],'symbol':lesson['symbol'],'decision_at':lesson['decision_at'],'outcome_at':lesson['outcome_at'],'student_action':student_action,'student_net_return':student['net_return'],'student_market_pnl':gross(student),'student_paid_cost':student['paid_cost'],'best_hindsight_teacher':teacher['action'],'teacher_rule_mechanism':teacher['rule_mechanism'],'teacher_net_return':teacher['net_return'],'market_exposure_advantage':market_advantage,'additional_cost':additional_cost,'net_advantage':gap,'reason_codes':reasons,'interpretation':'Matched historical accounting, not a proven predictive cause; hindsight winner is revealed only after maturity'}

def consolidate(run,candles,core):
    run=Path(run);core=Path(core);target=run/'consolidated_after_feedback.sqlite3'
    if target.exists():raise FileExistsError('Preserve prior consolidation')
    sys.path.insert(0,str(core.resolve()))
    import torch
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
    from bean.optimization.autodev import Journal
    from bean.optimization.sponge_dqn import SpongeDQN
    from bean.optimization.sponge_codec import pack
    c=load_candles(candles);features=Features(c);cutoff=int(utc_seconds('2025-10-01T00:00:00Z'));lessons=[];comparisons=[];seen=set()
    for line in (run/'decisions.jsonl').read_text().splitlines():
        record=json.loads(line)['payload']
        if record['phase']!='coached_advantage':continue
        i=int((record['decision_at']-86400-c.times[0])//86400);j=c.symbols.index(record['symbol']);exposure=record['vector'][15]
        lesson=mentor_lesson(features,i,j,exposure);validate_lesson(lesson,cutoff)
        match=matched_comparison(lesson,record['action'])
        if not np.isclose(match['student_net_return'],record['matured_net_return'],atol=1e-10,rtol=1e-10):raise ValueError('Matched reconstruction differs from actual student loss')
        if lesson['id'] in seen:raise ValueError('Repeated student feedback')
        seen.add(lesson['id']);lessons.append(lesson);comparisons.append(match)
    shutil.copyfile(run/'coached_advantage.sqlite3',target);journal=Journal(target);learner=SpongeDQN.recover_latest(journal);before={'updates':learner.updates,'frames':learner.frames,'weights_sha256':learner.weights_digest()};tip='0'*64
    for lesson in lessons:
        for action,case in enumerate(lesson['cases']):learner.observe(lesson['vector'],action,case['advantage_over_hold'],None,True)
        for _ in range(4):learner.train_batch()
        tip=hashlib.sha256((tip+'\n'+lesson['id']).encode()).hexdigest()
    capsule=pack(learner.training_state());objective='aiscend.mentor.unscored_training.v1';journal.append({'objective':objective,'iteration':0,'training_state':capsule,'online_weights_sha256':learner.weights_digest(),'updates':learner.updates,'frames':learner.frames,'metadata':{'knowledge_cutoff':cutoff,'economic_qualification':'UNEVALUATED_AFTER_CONSOLIDATION','feedback_lessons':len(lessons),'feedback_chain':tip,'no_post_consolidation_performance_evaluation':True}});journal.close()
    with gzip.GzipFile(filename=str(run/'student_vs_mentor_outcomes.jsonl.gz'),mode='wb',mtime=0) as dest:
        for row in comparisons:dest.write((canonical(row)+'\n').encode())
    with gzip.GzipFile(filename=str(run/'student_feedback_lessons.jsonl.gz'),mode='wb',mtime=0) as dest:
        for row in lessons:dest.write((canonical(row)+'\n').encode())
    with target.open('rb') as src,gzip.GzipFile(filename=str(target)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    state={'updates':learner.updates,'frames':learner.frames,'replay':len(learner.replay),'weights_sha256':learner.weights_digest(),'knowledge_cutoff':cutoff}
    manifest_path=run/'retention_manifest.json';manifest=json.loads(manifest_path.read_text());manifest['policies'].append({'id':'consolidated_after_feedback','file':target.name+'.gz','file_sha256':hashlib.sha256(Path(str(target)+'.gz').read_bytes()).hexdigest(),'knowledge_cutoff':'2025-10-01T00:00:00Z','state':state,'objective':objective,'economic_qualification':'UNEVALUATED_AFTER_CONSOLIDATION'});manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    from collections import Counter
    report={'schema':'aiscend.bean.mentor_consolidation.v1','prior_state':before,'retained_state':state,'actual_student_decisions_reconciled':len(comparisons),'counterfactual_strategy_feedback':len(comparisons)*len(ACTIONS),'feedback_chain':tip,'reason_counts':dict(Counter(code for x in comparisons for code in x['reason_codes'])),'actual_weights_changed':before['weights_sha256']!=state['weights_sha256'],'performance_after_consolidation':'NOT_EVALUATED','general_causal_understanding_proven':False,'live_orders':False}
    (run/'consolidation_receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--candles',type=Path,required=True);p.add_argument('--core-dir',type=Path,required=True);a=p.parse_args();consolidate(a.run,a.candles,a.core_dir)
