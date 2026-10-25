"""Compare the fast path with the frozen pre-optimization implementation.

Synthetic mechanics/performance fixtures only. No trading-performance claim.
"""
from __future__ import annotations
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from src.bean_causal_simulation import DecisionLedger
from src.bean_wide_history import DAY,canonical
from src import bean_multistep_environment as fast_env
from src import bean_wide_multistep as fast_run
from tests.test_bean_strategy_curriculum import data

META={'base_increment':'.00000001','min_market_funds':'1'}
EXPERTS=('cash','buy_hold','price_trend','donchian_breakout')

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);obj=importlib.util.module_from_spec(spec);sys.modules[name]=obj;spec.loader.exec_module(obj);return obj

def reference(root):
    env=module('src._frozen_speed_environment',Path(root)/'src/bean_multistep_environment.py')
    runner=module('src._frozen_speed_runner',Path(root)/'src/bean_wide_multistep.py')
    for name in ('VenueAccount','features_for','observation','fixed_action','teaching_rows','withheld','branch','valid_path'):setattr(runner,name,getattr(env,name))
    return env,runner

def fixture(env,*,gap=False):
    c=data();rows=[]
    for i,t in enumerate(c.times):
        if gap and 280<=i<=282:continue
        rows.append(dict(open_time=int(t),**{k:float(a[i,0]) for k,a in c.data.items()}))
    return env.features_for('X-USD',rows,0,440*DAY)

def rows(env,f):return list(env.teaching_rows('X-USD',f,META,400*DAY,EXPERTS,initial_capitals=(1000.,1000./389)))

def evaluate(runner,env,f):
    with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
        ledger=DecisionLedger(Path(d)/'decisions.sqlite3')
        result=runner.evaluate({'X-USD':f},{'X-USD':META},['X-USD'],261*DAY,400*DAY,'price_trend',ledger)
        evidence=[x[0] for x in ledger.db.execute('SELECT payload_json FROM decisions ORDER BY seq')];tip=ledger.tip;ledger.close()
        return result,evidence,tip

def benchmark(reference_root,output,*,core=None):
    old_env,old_run=reference(reference_root);checks=[]
    for gap in (False,True):
        old,new=fixture(old_env,gap=gap),fixture(fast_env,gap=gap)
        before,after=rows(old_env,old),rows(fast_env,new)
        if canonical(before)!=canonical(after):raise AssertionError('Fast teaching changed source bytes')
        a,b=evaluate(old_run,old_env,old),evaluate(fast_run,fast_env,new)
        if a!=b:raise AssertionError('Fast evaluation changed actions, costs, wealth or original ledger events')
        checks.append(dict(gap_fixture=gap,rows=len(before),teaching_sha256=hashlib.sha256(canonical(before).encode()).hexdigest(),decision_tip=a[2],byte_identical=True))
    def elapsed(env):
        f=fixture(env);rows(env,f) # warm caches for a repeated-search workload
        start=time.perf_counter();n=0
        for _ in range(12):n+=len(rows(env,f))
        return time.perf_counter()-start,n
    old_time,count=elapsed(old_env);new_time,count2=elapsed(fast_env)
    if count!=count2:raise AssertionError('Unequal speed workload')
    core_check=None
    if core:
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b,contextlib.redirect_stdout(io.StringIO()):
            product=next(f'ASSET{i}-USD' for i in range(100) if not fast_env.withheld(f'ASSET{i}-USD'))
            left,lt,_=old_run.train_models(core,{product:fixture(old_env)},{product:META},Path(a),400*DAY,'same-protocol')
            right,rt,_=fast_run.train_models(core,{product:fixture(fast_env)},{product:META},Path(b),400*DAY,'same-protocol')
            if lt!=rt or any(left[k].training_state()!=right[k].training_state() for k in left):raise AssertionError('Fast path changed actual Core gradients/replay/optimizer/RNG')
            core_check=dict(full_states_identical=True,models=list(left),rows=lt['rows'])
    report=dict(schema='aiscend.bean.fast_benchmark.v1',reference_commit='e49bb7e1052181a80be06ed23fd26316deae3ceb',scope='Synthetic mechanics and repeated-search throughput, not measured trading performance',checks=checks,actual_core=core_check,teaching_rows_timed=count,reference_seconds=old_time,fast_seconds=new_time,teaching_speedup=old_time/new_time)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n');print(canonical(report));return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reference-root',type=Path,required=True);p.add_argument('--core-dir',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();benchmark(a.reference_root,a.output,core=a.core_dir)
