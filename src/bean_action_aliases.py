"""Tie estimates for actions known to produce identical funded transactions."""
from __future__ import annotations
import math
from .bean_strategy_curriculum import ACTIONS

def consistent_values(vector,values):
    if len(vector)!=47 or len(values)!=len(ACTIONS) or not all(math.isfinite(x) for x in values):raise ValueError('Invalid action-value observation')
    exposure=vector[15];groups={}
    for a,target in enumerate(vector[36:]):
        key=('hold',) if a==1 and exposure not in (0.,1.) else ('target',exposure if a==1 else target)
        groups.setdefault(key,[]).append(a)
    result=list(values)
    for key,indices in groups.items():
        shared=0. if exposure==0. and key==('target',0.) else sum(values[a] for a in indices)/len(indices)
        for a in indices:result[a]=shared
    return result
