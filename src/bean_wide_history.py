"""Freeze Coinbase's entire public dollar-quote catalog and actual daily OHLCV.

Reuse the existing catalog policy. Missing intervals remain missing. The raw
responses, failures and current-vintage listing metadata are retained together.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlencode, quote
from .bean_strategy_lab import _get_json
from .universe_research import discover_public_spot, _catalog_hash

DAY=86400
START=int(datetime(2021,10,1,tzinfo=timezone.utc).timestamp())
END=int(datetime(2026,10,24,tzinfo=timezone.utc).timestamp())

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)

def save_gzip(path,value):
    with gzip.GzipFile(filename=str(path),mode='wb',mtime=0) as f:
        f.write(canonical(value).encode())

def candle_rows(payload,start,end):
    if not isinstance(payload,list):raise ValueError('Non-list candle response')
    rows={}
    for item in payload:
        if not isinstance(item,list) or len(item)!=6:raise ValueError('Invalid OHLCV row')
        stamp=int(item[0]);low,high,op,close,vol=map(float,item[1:])
        if not start<=stamp<end:continue
        if stamp%DAY or not all(math.isfinite(x) for x in (low,high,op,close,vol)) or not 0<low<=min(op,close)<=max(op,close)<=high or vol<0:
            raise ValueError('Invalid OHLCV geometry, timestamp or volume')
        row=dict(open_time=stamp,open=op,high=high,low=low,close=close,volume=vol)
        if stamp in rows and rows[stamp]!=row:raise ValueError('Conflicting candle duplicate')
        rows[stamp]=row
    return rows

def collect_product(product,start,end,directory,*,fetcher=_get_json,pause=.3):
    directory=Path(directory);requests=[];rows={};errors=[]
    for cursor in range(start,end,290*DAY):
        stop=min(end,cursor+290*DAY)
        iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
        url='https://api.exchange.coinbase.com/products/'+quote(product,safe='-')+'/candles?'+urlencode(dict(start=iso(cursor),end=iso(stop),granularity=DAY))
        try:
            body=fetcher(url);parsed=candle_rows(body,cursor,stop)
            requests.append(dict(url=url,response=body));rows.update(parsed)
        except Exception as exc:
            error=dict(url=url,error=type(exc).__name__+': '+str(exc)[:250]);errors.append(error);requests.append(error)
        if pause:time.sleep(pause)
    ordered=[rows[t] for t in sorted(rows)]
    payload=dict(product=product,requested_start=start,requested_end=end,requests=requests,candles=ordered)
    path=directory/(product+'.json.gz');save_gzip(path,payload)
    return dict(product=product,file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),candles=len(rows),missing_days=(end-start)//DAY-len(rows),request_errors=errors,status='PARTIAL_API_FAILURE' if errors else ('OBSERVED' if rows else 'NO_HISTORY'),first=min(rows,default=None),last=max(rows,default=None))

def collect(output,*,start=START,end=END,fetcher=_get_json,workers=3,pause=.3):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve the earlier snapshot')
    if start%DAY or end%DAY or not start<end or end>int(time.time()//DAY)*DAY:raise ValueError('Only complete daily buckets are allowed')
    output.mkdir(parents=True);raw=fetcher('https://api.exchange.coinbase.com/products')
    catalog=discover_public_spot(fetcher=lambda url:raw);products=catalog['eligible_quote_markets']
    if not products:raise ValueError('Empty public catalog')
    snapshot=dict(schema='aiscend.bean.wide_history.v1',recorded_at=int(time.time()),start=start,end=end,catalog=catalog,catalog_hash=_catalog_hash(products),raw_catalog=raw,listing_scope='Current-vintage listings, not point-in-time or authenticated account permissions',source_docs='https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles')
    (output/'catalog.json').write_text(json.dumps(snapshot,indent=2)+'\n')
    results={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(collect_product,p,start,end,output,fetcher=fetcher,pause=pause):p for p in products}
        for future in as_completed(futures):
            p=futures[future];results[p]=future.result()
            print(canonical(dict(event='market_collected',**{k:results[p][k] for k in ('product','candles','status')})),flush=True)
    if set(results)!=set(products):raise AssertionError('Missing catalog attempts')
    report=dict(schema='aiscend.bean.wide_history_manifest.v1',catalog_hash=snapshot['catalog_hash'],attempted=len(results),candles=sum(x['candles'] for x in results.values()),markets=dict(sorted(results.items())),live_orders=False)
    (output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(canonical(dict(event='wide_history_complete',attempted=report['attempted'],candles=report['candles'])),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();collect(a.output)
