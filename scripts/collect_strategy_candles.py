"""Collect checksum-verified official daily OHLCV, excluding the opened final year."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import time
import urllib.request
import zipfile

SYMBOLS=("BTCUSDT","ETHUSDT","ADAUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LTCUSDT","LINKUSDT")
START="2020-10"; END="2025-09"

def months(start=START,end=END):
    y,m=map(int,start.split("-"))
    while f"{y:04}-{m:02}"<=end:
        yield f"{y:04}-{m:02}"
        y,m=(y+1,1) if m==12 else (y,m+1)

def seconds(value):
    n=int(value)
    return n//(1_000_000 if n>10**14 else 1000)

def parse_archive(symbol,content):
    out=[]
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):continue
            for r in csv.reader(io.StringIO(z.read(name).decode())):
                if not r or not r[0].isdigit():continue
                stamp=seconds(r[0]); o,h,l,c,v=map(float,r[1:6])
                if not (all(math.isfinite(x) for x in (o,h,l,c,v)) and 0<l<=min(o,c)<=max(o,c)<=h and v>=0):raise ValueError("Invalid OHLCV candle")
                out.append({"symbol":symbol,"open_time":stamp,"available_at":stamp+86400,"open":o,"high":h,"low":l,"close":c,"volume":v,"quote_volume":float(r[7]),"trades":int(r[8])})
    return out

def fetch(symbol,month):
    name=f"{symbol}-1d-{month}.zip"
    url=f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d/{name}"
    last=None
    for attempt in range(4):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"AIscend-Research/1.0"})
            raw=urllib.request.urlopen(req,timeout=30).read()
            checksum=urllib.request.urlopen(url+".CHECKSUM",timeout=30).read().decode().split()[0]
            sha=hashlib.sha256(raw).hexdigest()
            if checksum!=sha:raise ValueError("Official checksum differs")
            return name,raw,parse_archive(symbol,raw),{"symbol":symbol,"month":month,"url":url,"sha256":sha,"checksum_verified":True,"bytes":len(raw)}
        except Exception as e:
            last=e
            if attempt<3:time.sleep(1+attempt)
    raise RuntimeError(f"{symbol}/{month}: {last}")

def collect(output):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError("Do not replace prior source evidence")
    output.mkdir(parents=True,exist_ok=True)
    rows=[];receipts=[]
    with zipfile.ZipFile(output/"source_archives.zip","w",compression=zipfile.ZIP_STORED) as archive,ThreadPoolExecutor(max_workers=6) as pool:
        jobs=[pool.submit(fetch,s,m) for s in SYMBOLS for m in months()]
        for n,job in enumerate(as_completed(jobs),1):
            name,raw,data,receipt=job.result();archive.writestr(name,raw);rows.extend(data);receipts.append(receipt)
            if n%40==0:print(json.dumps({"collected_monthlies":n,"requested":len(jobs)}),flush=True)
    rows.sort(key=lambda x:(x["open_time"],x["symbol"]))
    if len({(r["symbol"],r["open_time"]) for r in rows})!=len(rows):raise ValueError("Duplicate candles")
    lines="".join(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n" for r in rows).encode()
    with gzip.GzipFile(filename=str(output/"candles.jsonl.gz"),mode="wb",mtime=0) as dest:dest.write(lines)
    manifest={"schema":"aiscend.strategy_candles.v1","source":"Official Binance Public Data spot monthly 1d klines","documentation":"https://github.com/binance/binance-public-data","source_terms":"https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md","symbols":list(SYMBOLS),"start_month":START,"end_month":END,"observed_at":datetime.now(timezone.utc).isoformat(),"candles":len(rows),"rows_sha256":hashlib.sha256(lines).hexdigest(),"candles_gzip_sha256":hashlib.sha256((output/"candles.jsonl.gz").read_bytes()).hexdigest(),"archives_sha256":hashlib.sha256((output/"source_archives.zip").read_bytes()).hexdigest(),"receipts":sorted(receipts,key=lambda x:(x["symbol"],x["month"])),"limitations":["Fixed present-selected eight-symbol example universe; survivorship and venue bias remain","USDT quotes are not USD quotes","Historical revised OHLCV; not live orders or orderbook fills","Entire study is development; prior experiments already inspected overlapping dates","No candles from the opened 2025-10-24 through 2026-10-23 final interval are collected"]}
    (output/"source_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"event":"candle_collection_complete","candles":len(rows),"rows_sha256":manifest["rows_sha256"]}),flush=True)
    return manifest

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",type=Path,required=True);a=p.parse_args();collect(a.output)
