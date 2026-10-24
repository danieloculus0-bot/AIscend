"""Score every explicit curriculum rule over the available five-year candle source."""
from __future__ import annotations
import argparse
import gzip
import json
from pathlib import Path
import shutil
from src.bean_strategy_curriculum import ACTIONS,Features,load_candles,evaluate
from src.bean_causal_simulation import DecisionLedger

def run(candles,output):
    output=Path(output)
    if output.exists():raise FileExistsError('Preserve prior expert scoreboard')
    output.mkdir(parents=True)
    c=load_candles(candles);features=Features(c);ledger=DecisionLedger(output/'expert_decisions.sqlite3')
    start=int(c.times[200])+86400;end=int(c.times[-1])+86400
    protocol={'schema':'aiscend.bean.explicit_expert_teachers.v1','scope':'Complete development scoreboard, all rules retained; not learned strategy performance','source_rows_sha256':c.source_hash,'actions':list(ACTIONS),'cost_per_side':.01,'first_decision_at':start,'last_source_available_at':end,'warmup_days':200,'source_duration':'2020-10 through 2025-09, with first 200 bars reserved for trailing features','capital':'Each rule independently starts eight separate $1000 accounts; equal-weight means are not a single account portfolio','selection':'No selection or re-training; every registered action is evaluated','live_orders':False}
    ledger.metadata('protocol',protocol)
    results={a:evaluate(features,None,start,end,ledger,'teacher_full_history/'+a,fixed=a) for a in ACTIONS}
    report={'protocol':protocol,'results':results,'decisions':ledger.seq,'decision_ledger_tip':ledger.tip,'future_profitability_proven':False};ledger.close()
    (output/'scoreboard.json').write_text(json.dumps(report,indent=2)+'\n')
    for p in output.iterdir():
        if p.suffix in ('.sqlite3','.jsonl'):
            with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    print(json.dumps({'event':'all_expert_teachers_complete','returns':{k:v['equal_weight_mean_return'] for k,v in results.items()},'decisions':ledger.seq}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candles',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.candles,a.output)
