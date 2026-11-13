"""Hold trained forecasts fixed and isolate the treatment of outstanding exit costs."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from src.bean_strategy_curriculum import Features,load_candles
from src.bean_economic_experiment import evaluate
from src.bean_economic_values import funded_values,market_vector
from src.bean_route_values import liquidation_values
from src.bean_causal_simulation import DecisionLedger
from src.strategy_atlas import canonical,digest,utc_seconds

class ForecastCache:
    def __init__(self,features,cases,cutoff,mode,weights_sha256):
        self.forecasts={};self.cutoff=cutoff;self.mode=mode;self.weights_sha256=weights_sha256;c=features.c
        for x in cases:
            if set(x)-{'symbol','decision_at','forecast'}:raise ValueError('Only saved predictions and their decision identity may enter the policy cache')
            i=int((x['decision_at']-86400-c.times[0])//86400);j=c.symbols.index(x['symbol']);v=market_vector(features.vector(i,j,0.));key=(x['decision_at'],digest(v))
            if key in self.forecasts and self.forecasts[key]!=x['forecast']:raise ValueError('Conflicting forecasts for the same market inputs')
            self.forecasts[key]=list(x['forecast'])
    def call(self,op,**kw):
        if op=='status':return {'source_model_weights':self.weights_sha256,'valuation':self.mode,'cutoff':self.cutoff,'new_training_updates':0}
        if op!='values' or kw['decision_at']<self.cutoff:raise ValueError('Future learned state or invalid operation')
        forecasts=[];values=[]
        for v in kw['vectors']:
            forecast=self.forecasts[(kw['decision_at'],digest(market_vector(v)))];forecasts.append(forecast)
            rows=liquidation_values(v,forecast) if self.mode=='net_liquidation' else funded_values(v,forecast)
            values.append([x['net_log_return'] for x in rows])
        return {'values':values,'market_forecasts':forecasts}

def run(source,candles,output):
    source=Path(source);output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve earlier exit-cost audit')
    output.mkdir(parents=True);prior=json.loads((source/'results.json').read_text());protocol=json.loads((source/'protocol.json').read_text());features=Features(load_candles(candles))
    if features.c.source_hash!=protocol['source_sha256']:raise ValueError('Market source differs from the trained forecast source')
    retained=json.loads((source/'retention_manifest.json').read_text())['policies'];ledger=DecisionLedger(output/'decisions.sqlite3');results=[]
    audit_protocol={'schema':'aiscend.bean.exit_cost_audit.v1','scope':'SEEN_HISTORY_ACCOUNTING_ABLATION_NO_NEW_TRAINING','source_protocol_sha256':protocol['protocol_sha256'],'forecast_source_results_sha256':hashlib.sha256((source/'results.json').read_bytes()).hexdigest(),'implementation':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ('src/bean_route_values.py','scripts/bean_exit_cost_audit.py')},'comparison':'Same saved neural forecasts, data, capital, reference action and trade threshold; only forecast valuation changes from gross mark to net liquidation. Observed exit fees are charged in both simulations. No future outcome label is loaded into ForecastCache.','promotion':'No automatic promotion or fresh profitability claim','live_orders':False};audit_protocol['protocol_sha256']=digest(audit_protocol);ledger.metadata('protocol',audit_protocol)
    for row in prior['results']:
        fold=row['fold'];start=int(utc_seconds(row['start']));end=int(utc_seconds(row['end']));original=row['models']['factor']['result'];model=next(x for x in retained if x['id']==f'fold-{fold}-factor');pair={}
        for mode in ('gross_mark','net_liquidation'):
            known_predictions=[{k:x[k] for k in ('symbol','decision_at','forecast')} for x in original['forecast_cases']]
            worker=ForecastCache(features,known_predictions,start,mode,model['online_weights_sha256'])
            pair[mode]=evaluate(features,start,end,ledger,f'fold-{fold}/'+mode,worker=worker)
        if abs(pair['gross_mark']['ending_equity']-original['ending_equity'])>1e-6:raise ValueError('Cached neural forecasts do not reproduce the original financial path')
        results.append({'fold':fold,'start':row['start'],'end':row['end'],'source_model_weights_sha256':model['online_weights_sha256'],'original_reproduced':True,'results':pair,'exit_reserve_effect_on_net_return':pair['net_liquidation']['net_return']-pair['gross_mark']['net_return']})
    report={'schema':'aiscend.bean.exit_cost_results.v1','protocol':audit_protocol,'results':results,'actual_new_training_updates':0,'independent_profitability_proven':False,'decision_records':ledger.seq,'decision_ledger_tip':ledger.tip,'live_orders':False};ledger.metadata('results',report);ledger.close();(output/'results.json').write_text(json.dumps(report,indent=2)+'\n');(output/'protocol.json').write_text(json.dumps(audit_protocol,indent=2)+'\n')
    for p in list(output.iterdir()):
        if p.suffix in ('.sqlite3','.jsonl'):
            with p.open('rb') as src,gzip.GzipFile(filename=str(p)+'.gz',mode='wb',mtime=0) as dest:shutil.copyfileobj(src,dest)
    lines=['# Exit-cost reserve audit\n','This is a same-forecast accounting comparison on already inspected development history. There are no new neural updates. The cached gross-mark policy must reproduce the original final equity exactly within 1e-6 before the comparison is accepted.\n','The old scenario value paid current orders but valued remaining inventory gross. The new value subtracts the expected closing fee from held inventory at both the current and future mark. This counts the round trip for a new position while avoiding repeated exit charges for an unchanged position. Both actual simulations still charge the same observed orders and final liquidation fees.\n','| Window | Original scenario return | Net-liquidation scenario return | Difference |','| --- | ---: | ---: | ---: |']
    for r in results:lines.append(f"| {r['start'][:4]} | {r['results']['gross_mark']['net_return']:+.2%} | {r['results']['net_liquidation']['net_return']:+.2%} | {r['exit_reserve_effect_on_net_return']:+.2%} |")
    lines.append('\nAccounting correctness and financial advantage are separate. All forecasts were generated by the previously trained Core model; no future outcomes enter the cached policy. A positive segment is not a new unseen evaluation or proof of beating other traders. Full decisions and weight-source hashes remain archived.\n');(output/'RESULTS.md').write_text('\n'.join(lines));print(canonical({'event':'exit_cost_audit_complete','effects':[x['exit_reserve_effect_on_net_return'] for x in results]}),flush=True);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--candles',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.source,a.candles,a.output)
