"""Small reversible pair-code score index over immutable, full experiment evidence.

Eight two-character table lookups form one 16-character mnemonic. Scores and
source hashes retain full precision; codes are references, not numeric rounding.
The existing DecisionLedger remains the authoritative append-only event stream.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from .bean_causal_simulation import DecisionLedger
from .bean_wide_history import canonical

ALPHABET='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
FIELDS=('method','dataset','window','seed','cost','configuration','evidence','reason')
REASONS={'P0':'positive return and improves on matched control','P1':'positive return but trails matched control','L0':'losing return but improves on matched control','L1':'losing return and trails matched control','T0':'exact matched tie','D0':'incomplete or unmatched evidence'}

def pair(number):
    if not 0<=number<len(ALPHABET)**2:raise OverflowError('Pair dictionary full; start a new ledger page, never reuse a code')
    return ALPHABET[number//len(ALPHABET)]+ALPHABET[number%len(ALPHABET)]

def score(candidate,baseline):
    values=[candidate.get('net_return'),baseline.get('net_return'),candidate.get('drawdown')]
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in values):raise ValueError('Missing or nonfinite score metrics')
    keys=('start','end','markets','starting_equity')
    matched=all(k in candidate and candidate[k]==baseline.get(k) for k in keys)
    if 'market_outcomes' in candidate or 'market_outcomes' in baseline:matched=matched and set(candidate.get('market_outcomes',{}))==set(baseline.get('market_outcomes',{}))
    delta=candidate['net_return']-baseline['net_return'] if matched else None
    reason='D0' if not matched else 'T0' if delta==0 else ('P0' if delta>0 else 'P1') if candidate['net_return']>0 else ('L0' if delta>0 else 'L1')
    return dict(net_return=candidate['net_return'],drawdown=candidate['drawdown'],baseline_return=baseline['net_return'],incremental_return_bps=None if delta is None else delta*10000,reason=reason,matched=matched)

class CompactScores:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.events=DecisionLedger(self.root/'events.sqlite3');self.db=sqlite3.connect(self.root/'scores.sqlite3')
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE IF NOT EXISTS tokens(field TEXT,code TEXT,value_json TEXT,PRIMARY KEY(field,code),UNIQUE(field,value_json));
CREATE TABLE IF NOT EXISTS scores(receipt TEXT PRIMARY KEY,mnemonic TEXT UNIQUE,role TEXT,available_at INTEGER,recorded_at INTEGER,method TEXT,window TEXT,net_return REAL,baseline_return REAL,incremental_return_bps REAL,drawdown REAL,reason TEXT,payload_json TEXT);
CREATE INDEX IF NOT EXISTS comparable_scores ON scores(role,available_at,window,incremental_return_bps DESC);''')
        # Rebuild the disposable SQL index from the actual event archive.
        self.db.execute('DELETE FROM scores');self.db.execute('DELETE FROM tokens')
        for (raw,) in self.events.db.execute('SELECT payload_json FROM decisions ORDER BY seq'):self._index(json.loads(raw))
        self.db.commit()
    def token(self,field,value):
        raw=canonical(value);found=self.db.execute('SELECT code FROM tokens WHERE field=? AND value_json=?',(field,raw)).fetchone()
        if found:return found[0]
        code=pair(self.db.execute('SELECT count(*) FROM tokens WHERE field=?',(field,)).fetchone()[0])
        self.db.execute('INSERT INTO tokens VALUES (?,?,?)',(field,code,raw));return code
    def _index(self,event):
        codes=''.join(self.token(field,event['keys'][field]) for field in FIELDS)
        if codes!=event['mnemonic']:raise ValueError('Pair dictionary order differs from the authoritative archive')
        s=event['score'];p=event
        self.db.execute('INSERT INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(p['receipt'],codes,p['role'],p['available_at'],p['recorded_at'],p['keys']['method'],canonical(p['keys']['window']),s['net_return'],s['baseline_return'],s['incremental_return_bps'],s['drawdown'],s['reason'],canonical(p)))
    def record(self,*,keys,candidate,baseline,role,available_at,source):
        if role not in ('training','development','evaluation'):raise ValueError('Explicit evidence role required')
        if set(keys)!=set(FIELDS)-{'reason'}:raise ValueError('Expected exactly seven source identity fields')
        s=score(candidate,baseline);keys={**keys,'reason':s['reason']}
        payload=dict(keys=keys,score=s,role=role,available_at=int(available_at),source=source)
        receipt=hashlib.sha256(canonical(payload).encode()).hexdigest()
        found=self.db.execute('SELECT mnemonic FROM scores WHERE receipt=?',(receipt,)).fetchone()
        if found:return dict(receipt=receipt,mnemonic=found[0],new_record=False,new_credit=False)
        self.db.execute('SAVEPOINT candidate')
        codes=''.join(self.token(field,keys[field]) for field in FIELDS)
        collision=self.db.execute('SELECT receipt FROM scores WHERE substr(mnemonic,1,14)=?',(codes[:14],)).fetchone()
        if collision:
            self.db.execute('ROLLBACK TO candidate');self.db.execute('RELEASE candidate')
            raise ValueError('One evidence identity cannot acquire conflicting outcomes')
        event=dict(**payload,receipt=receipt,mnemonic=codes,recorded_at=int(time.time()))
        self.events.record(event);self._index(event);self.db.execute('RELEASE candidate');self.db.commit()
        return dict(receipt=receipt,mnemonic=codes,new_record=True,new_credit=role=='training' and s['matched'])
    def decode(self,mnemonic):
        if len(mnemonic)!=16:raise ValueError('Expected eight complete character pairs')
        result={}
        for i,field in enumerate(FIELDS):
            row=self.db.execute('SELECT value_json FROM tokens WHERE field=? AND code=?',(field,mnemonic[2*i:2*i+2])).fetchone()
            if row is None:raise KeyError('Unknown pair in '+field)
            result[field]=json.loads(row[0])
        return result
    def rank(self,as_of,*,window,limit=20,historical=False):
        # Evaluation rows are retained but never enter optimization ranking.
        query="SELECT mnemonic,method,net_return,incremental_return_bps,drawdown,reason,receipt FROM scores WHERE role='training' AND available_at<=? AND window=? AND incremental_return_bps IS NOT NULL"
        args=[int(as_of),canonical(window)]
        if not historical:query+=' AND recorded_at<=?';args.append(int(as_of))
        query+=' ORDER BY incremental_return_bps DESC,drawdown,receipt LIMIT ?';args.append(min(100,max(1,int(limit))))
        return [dict(x) for x in self.db.execute(query,args)]
    def export(self):
        self.events.snapshot();self.db.commit()
        legend={field:{r[0]:json.loads(r[1]) for r in self.db.execute('SELECT code,value_json FROM tokens WHERE field=? ORDER BY code',(field,))} for field in FIELDS}
        (self.root/'legend.json').write_text(json.dumps(dict(schema='aiscend.bean.pair_scores.v1',fields=FIELDS,reasons=REASONS,dictionaries=legend),indent=2)+'\n')
        rows=[dict(x) for x in self.db.execute('SELECT mnemonic,role,method,window,net_return,incremental_return_bps,drawdown,reason,receipt FROM scores ORDER BY receipt')]
        (self.root/'scoreboard.json').write_text(json.dumps(dict(schema='aiscend.bean.compact_scoreboard.v1',trials=len(rows),rows=rows),indent=2)+'\n')
        return rows
    def close(self):self.export();self.events.close();self.db.close()

def index_report(source,output):
    source=Path(source);raw=source.read_bytes();report=json.loads(raw);sha=hashlib.sha256(raw).hexdigest();ledger=CompactScores(output)
    # The broad study's later windows remain evaluation, not search rewards.
    for group in report['results']:
        control=group['results']['training_selected_expert']
        for method,result in group['results'].items():
            keys=dict(method=method,dataset=report['scope'],window=dict(period=group['period'],cohort=group['cohort'],start=result['start'],end=result['end']),seed=41,cost=.01,configuration=report['protocol_sha256'],evidence=dict(sha256=sha,pointer=f"/results/{report['results'].index(group)}/results/{method}"))
            ledger.record(keys=keys,candidate=result,baseline=control,role='evaluation',available_at=result['end'],source=dict(path=str(source),sha256=sha,qualification='Historical simulation, not verified market profitability'))
    rows=ledger.export();ledger.close();return dict(trials=len(rows),source_sha256=sha)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--results',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(canonical(index_report(a.results,a.output)))
