"""Build the outcome index from immutable, provenance-tagged result rows."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

def build(directory,source_root=None):
    directory=Path(directory);data=json.loads((directory/'source_summary.json').read_text())
    if not all(x['recovered'] for x in data['checkpoints']):raise ValueError('An upstream checkpoint lacks a successful recovery receipt')
    if source_root:
        root=Path(source_root)
        for source in data['sources']:
            body=(root/source['path']).read_bytes();sha=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
            if sha!=source['blob_sha']:raise ValueError('Source file differs from the pinned Git blob: '+source['path'])
        for row in data['outcomes']:
            source=json.loads((root/row['source_path']).read_text());value=source
            for key in row['source_pointer'].strip('/').split('/'):value=value[int(key)] if isinstance(value,list) else value[key]
            if value['ending_equity']!=row['ending_equity'] or value['net_return']!=row['net_return'] or value['label']!=row['label']:raise ValueError('Summary row differs from its exact source case')
    path=directory/'learning_outcomes.sqlite3';path.unlink(missing_ok=True);con=sqlite3.connect(path)
    con.execute('CREATE TABLE provenance(name TEXT PRIMARY KEY,path TEXT,source_ref TEXT,git_blob_sha TEXT)')
    con.execute('CREATE TABLE outcomes(method TEXT,window TEXT,method_type TEXT,starting_equity REAL,ending_equity REAL,net_return REAL,paid_costs REAL,orders INTEGER,drawdown REAL,source_path TEXT,source_pointer TEXT,source_ref TEXT,qualification TEXT,details_json TEXT,PRIMARY KEY(method,window))')
    con.execute('CREATE TABLE checkpoints(phase TEXT,id TEXT,scheme TEXT,knowledge_cutoff INTEGER,updates INTEGER,input_rows INTEGER,replay_entries INTEGER,sampled_source_rows INTEGER,evicted_unsampled INTEGER,recovered INTEGER,weights_changed INTEGER,weights_sha256 TEXT,state_path TEXT,state_sha256 TEXT,source_ref TEXT,qualification TEXT,PRIMARY KEY(phase,id))')
    for x in data['sources']:con.execute('INSERT INTO provenance VALUES (?,?,?,?)',(x['name'],x['path'],x['ref'],x['blob_sha']))
    for x in data['outcomes']:
        if x['starting_equity']!=1000 or abs(x['ending_equity']/1000-1-x['net_return'])>1e-9:raise ValueError('Invalid funded-equity summary')
        con.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(x.get(k) for k in ('method','window','method_type','starting_equity','ending_equity','net_return','paid_costs','orders','drawdown','source_path','source_pointer','source_ref','qualification'))+(json.dumps(x,sort_keys=True,separators=(',',':')),))
    for x in data['checkpoints']:con.execute('INSERT INTO checkpoints VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(x.get(k) for k in ('phase','id','scheme','knowledge_cutoff','updates','input_rows','replay_entries','sampled_source_rows','evicted_unsampled','recovered','weights_changed','weights_sha256','state_path','state_sha256','source_ref','qualification')))
    con.commit()
    if con.execute('pragma integrity_check').fetchone()[0]!='ok':raise ValueError('Index failed integrity check')
    counts={table:con.execute('select count(*) from '+table).fetchone()[0] for table in ('provenance','outcomes','checkpoints')};con.close()
    with gzip.GzipFile(filename=str(path)+'.gz',mode='wb',mtime=0) as dest:dest.write(path.read_bytes())
    methods=sorted({x['method'] for x in data['outcomes']});lookup={(x['method'],x['window'][:4]):x for x in data['outcomes']}
    lines=['# BEAN learning push: complete outcome and checkpoint index\n','This index records every tested method, including losses. It does not select a hindsight winner or establish best-on-market performance. All three April-through-September windows are already inspected historical development periods on eight present-selected Binance USDT instruments. The modeled bankroll is 1,000 quote units split into funded sleeves; USD conversion and executable exchange fills are not verified.\n','## Main findings\n','Chunked ingestion with gradient digestion improved on ingest-then-train in all three tested windows. Complete-row sampling is now verified in six additional candidates, but complete coverage did not consistently improve returns. Equivalent-action constraints and exit-cost/reward changes also produced mixed financial results. Accounting and training correctness are necessary conditions, not evidence of a market-prediction edge. No revised model has been independently qualified or activated for live trading.\n','## Every development outcome\n','| Method | 2023 Apr-Sep | 2024 Apr-Sep | 2025 Apr-Sep |','| --- | ---: | ---: | ---: |']
    for method in methods:lines.append('| '+method+' | '+' | '.join(f"{lookup[(method,year)]['net_return']:+.2%}" for year in ('2023','2024','2025'))+' |')
    lines += ['\nThe training-selected expert varies by window. Its fallback profit belongs to that rule, not to the neural model. Constant past-mean forecasts and buy/hold are retained as simple controls. Different methods and repeated source cases are not independent market evidence.\n','## Retention and verification\n',f"The index contains {counts['outcomes']} outcome records and {counts['checkpoints']} current checkpoint references, backed by {counts['provenance']} immutable upstream result/manifest files. All referenced checkpoint receipts report successful full-state recovery, including optimizer, target, RNG and replay. Earlier replicated experiments remain in their original folders and Git history.\n",f"There are {data['raw_action_labels']:,} raw action/exposure labels over {data['distinct_market_events']:,} distinct asset/period outcomes; the latter are still correlated across coins and dates. Every source row in each full-pass candidate reached an actual training batch before eviction. This proves data participation, not that each case was understood or profitably generalized.\n",'`learning_outcomes.sqlite3.gz` contains outcome, checkpoint and provenance tables. Each outcome includes its exact upstream JSON pointer, pinned source commit and Git blob hash. No missing individual events were reconstructed from aggregate totals. The original complete decision ledgers and model files remain upstream.\n','Rebuild with `python research/bean_learning_push_20261024/build_summary.py`. For source verification, pass `--source-root /path/to/checkout` checked out at the pinned source commit. Full source paths and hashes are in `source_summary.json`.\n','## Next qualification push\n','Use the corrected training/valuation contracts on broader venue-eligible data, with realistic quote conversion, order minimums, spreads and missing-market handling. Keep new asset/date evaluation separate from the development corpus. Add multi-step trade management so holding-period decisions and future execution costs are represented explicitly. Compare fixed experts, simple predictive baselines and BEAN under identical conditions before claiming learned improvement. The existing full Coinbase collectors and broader archived price history should be reused, not replaced by another small-market sample. Prospective paper performance and independently comparable external benchmarks are required before any market-rank claim.\n']
    lines.append('## Using a retained model\n\nUse BEAN Core revision `887935ae816b4ce6175c6cde54d749f0e5cf3268` and verify the state-file hash before `SpongeDQN.from_training_state`. Respect its knowledge cutoff and the matching source policy/valuation convention. The neural head alone is not the complete trader: factored heads predict market components, and raw-head policies may require alias constraints or an expert reference. This index does not auto-activate a model or update the hosted BEAN notes connector.\n')
    (directory/'README.md').write_text('\n'.join(lines)+'\n')
    files={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(directory.iterdir()) if p.is_file() and p.name not in ('learning_outcomes.sqlite3','artifacts_manifest.json')}
    (directory/'artifacts_manifest.json').write_text(json.dumps({'schema':'aiscend.bean.learning_summary_artifacts.v1','counts':counts,'files':files},indent=2)+'\n');print(json.dumps(counts));return counts

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path);a=p.parse_args();build(Path(__file__).parent,a.source_root)
