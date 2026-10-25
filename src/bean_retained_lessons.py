"""Matched trading lessons and bounded cross-asset recall over immutable evidence.

Reuse PatternMemory's nearest-case calculation and the existing pair-code legend.
Historical outcomes are labels and recalled mature evidence, never future inputs.
"""
from __future__ import annotations
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import numpy as np
from .bean_strategy_curriculum import PatternMemory
from .bean_compact_scores import pair
from .bean_multistep_environment import withheld
from .bean_wide_history import DAY,canonical,save_gzip

def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

def regime(vector):
    """Only visible exposure, trend, volatility and order-affordability context."""
    if len(vector)!=43:raise ValueError('Expected funded 43-input trade state')
    bits=(vector[35]>=.5,vector[8]>0,vector[9]>1.,vector[41]>.1)
    return pair(sum(int(b)<<i for i,b in enumerate(bits)))

def match_lesson(cash,long):
    keys=('product','teacher','episode','decision_at','execution_at','outcome_at','wealth_before','vector','done')
    if cash['action']!=0 or long['action']!=1 or any(cash[k]!=long[k] for k in keys):raise ValueError('Unmatched starting state, path, or action pair')
    if not 0<cash['wealth_after'] or not 0<long['wealth_after']:raise ValueError('Invalid funded outcome')
    advantage=long['reward']-cash['reward']
    if not math.isclose(advantage,100*math.log(long['wealth_after']/cash['wealth_after']),abs_tol=1e-8):raise ValueError('Advantage differs from exact wealth ratio')
    effects={k:long['effects'][k]-cash['effects'][k] for k in cash['effects']}
    if not math.isclose(sum(effects.values()),long['wealth_after']-cash['wealth_after'],abs_tol=1e-7):raise ValueError('Matched effect attribution differs from net wealth')
    market=effects['pre_fill_market_pnl']+effects['post_fill_market_pnl']
    reason='EQ' if advantage==0 else 'TC' if market>=0 and advantage<0 else 'MK'
    return dict(id=digest([cash['id'],long['id']]),symbol=cash['product'],label_symbol=cash['product'],decision_at=cash['decision_at'],outcome_at=cash['outcome_at']+7*DAY,source_outcome_at=cash['outcome_at'],vector=cash['vector'],rewards=[0.,advantage],cash_reward=cash['reward'],long_reward=long['reward'],source_ids=[cash['id'],long['id']],regime=regime(cash['vector']),reason=reason,effect_delta=effects,teacher=cash['teacher'],raw_rows=(cash,long))

def load_lessons(corpus,cutoff):
    corpus=Path(corpus);manifest=json.loads((corpus/'artifacts_manifest.json').read_text())['files'];lessons=[];seen=set();source=hashlib.sha256()
    for path in sorted((corpus/'teaching').glob('*.jsonl.gz')):
        relative=str(path.relative_to(corpus));blob=path.read_bytes()
        if relative not in manifest or hashlib.sha256(blob).hexdigest()!=manifest[relative]['sha256']:raise ValueError('Changed teaching source')
        pending=[]
        for line in gzip.decompress(blob).splitlines():
            row=json.loads(line)
            if row['outcome_at']+7*DAY>cutoff:continue
            if withheld(row['product']) or not row['decision_at']<row['outcome_at'] or len(row['vector'])!=43 or row['id'] in seen:raise ValueError('Future, withheld, duplicate or invalid teaching row')
            if row['product']+'.jsonl.gz'!=path.name:raise ValueError('Source file and case asset differ')
            seen.add(row['id']);source.update(line+b'\n');pending.append(row)
            if len(pending)==2:
                lesson=match_lesson(*pending);lesson['source_file']=relative;lesson['source_file_sha256']=manifest[relative]['sha256'];lessons.append(lesson);pending=[]
        if pending:raise ValueError('Orphaned counterfactual action')
    if not lessons:raise ValueError('No mature matched lessons')
    return lessons,dict(source_rows=len(seen),pairs=len(lessons),source_sha256=source.hexdigest(),positive_advantages=sum(x['rewards'][1]>0 for x in lessons),negative_advantages=sum(x['rewards'][1]<0 for x in lessons),ties=sum(x['rewards'][1]==0 for x in lessons))

def public_lesson(lesson):
    return {k:v for k,v in lesson.items() if k!='raw_rows'}

def save_lessons(path,lessons):
    with gzip.GzipFile(filename=str(path),mode='wb',mtime=0) as dest:
        for lesson in lessons:dest.write((canonical(public_lesson(lesson))+'\n').encode())

def active_memory(lessons,cutoff,*,per_bucket=128,seed=41):
    """Reservoir sampling preserves gains and losses without selecting winners."""
    if not 1<=per_bucket<=128:raise ValueError('Active-memory pair-code page must stay within 3,844 entries')
    rng=random.Random(seed);buckets=defaultdict(list);counts=defaultdict(int)
    for lesson in lessons:
        if lesson['outcome_at']>cutoff:raise ValueError('Unmatured memory lesson')
        key=lesson['regime'];counts[key]+=1;bucket=buckets[key]
        if len(bucket)<per_bucket:bucket.append(lesson)
        else:
            index=rng.randrange(counts[key])
            if index<per_bucket:bucket[index]=lesson
    selected=[public_lesson(x) for key in sorted(buckets) for x in buckets[key]]
    for i,entry in enumerate(selected):entry['code']=pair(i)
    return dict(schema='aiscend.bean.active_lessons.v1',cutoff=cutoff,per_bucket=per_bucket,selection_seed=seed,source_pairs=len(lessons),entries=selected,reason_legend={'MK':'Relative market movement','TC':'Market gain overwhelmed by costs or reserve changes','EQ':'Exact economic tie'},regime_counts=dict(counts))

class RetainedMemory:
    def __init__(self,state,*,placebo=False):
        self.cutoff=state['cutoff'];self.state=state;self.sha256=digest(state);self.placebo=placebo;self.entries=state['entries'];self.buckets={}
        labels=list(self.entries)
        if placebo:random.Random(41).shuffle(labels)
        for entry,label in zip(self.entries,labels):
            item=dict(entry);item['label_symbol']=label['symbol'];item['outcome_at']=max(entry['outcome_at'],label['outcome_at']);item['rewards']=label['rewards'];item['label_code']=label['code']
            self.buckets.setdefault(item['regime'],[]).append(item)
        self.memories={key:PatternMemory(entries) for key,entries in self.buckets.items()}
    def query(self,vector,symbol,decision_at):
        if decision_at<self.cutoff:raise ValueError('Memory contains later knowledge')
        key=regime(vector);memory=self.memories.get(key)
        if memory is None:return dict(count=0,codes=[],label_codes=[],mean_advantage=0.,usable=False,regime=key,max_outcome_at=None)
        found=memory.query(vector,symbol,decision_at,k=32);indices=found.get('memory_group_ids',[]);entries=self.buckets[key]
        if found['count'] and any(entries[i]['symbol']==symbol or entries[i]['label_symbol']==symbol for i in indices):raise AssertionError('Same-asset evidence crossed the transfer boundary')
        usable=found['count']>=16 and found['mean_squared_distance']<=1.
        return dict(count=found['count'],codes=[entries[i]['code'] for i in indices],label_codes=[entries[i]['label_code'] for i in indices],mean_advantage=found['mean_rewards'][1] if found['count'] else 0.,usable=usable,regime=key,max_outcome_at=found['max_outcome_at'],mean_squared_distance=found.get('mean_squared_distance'),memory_sha256=self.sha256,placebo=self.placebo)

class RecallPolicy:
    """Expose actual consumed memory receipts through the existing evaluator."""
    def __init__(self,model,memory,cutoff):self.model=model;self.memory=memory;self.knowledge_cutoff=cutoff;self.updates=model.updates if model else 0
    def weights_digest(self):return digest(dict(model=self.model.weights_digest() if self.model else None,memory=self.memory.sha256,placebo=self.memory.placebo,mix=.25 if self.model else 1.))
    def predict(self,vectors,products,decision_at):
        if decision_at<self.knowledge_cutoff:raise ValueError('Policy contains later knowledge')
        if self.model:
            import torch
            with torch.no_grad():neural=self.model.online(torch.tensor(vectors,dtype=torch.float32)).tolist()
        else:neural=[[0.,0.] for _ in vectors]
        values=[];contexts=[]
        for vector,product,q in zip(vectors,products,neural):
            recall=self.memory.query(vector,product,decision_at);adv=q[1]-q[0]
            combined=.75*adv+.25*recall['mean_advantage'] if self.model and recall['usable'] else recall['mean_advantage'] if recall['usable'] else adv
            values.append([0.,combined]);contexts.append(dict(**recall,neural_advantage=adv,used_advantage=combined,source='mature retained matched cases',query_symbol=product))
        return dict(values=values,contexts=contexts)
