"""Collect current Coin Metrics daily PriceUSD and reported volume for research.

Community data: CC BY-NC 4.0. Records all attempted assets and failures; never
chooses assets by subsequent return. The result supplements a stale bulk archive.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError

lock=threading.Lock();next_read=0.

def fetch(url):
    global next_read
    for attempt in range(4):
        with lock:
            pause=max(0,next_read-time.monotonic());next_read=max(time.monotonic(),next_read)+.72
        if pause:time.sleep(pause)
        try:
            with urlopen(Request(url,headers={"User-Agent":"AIscend-Causal-Research/1.0"}),timeout=30) as r:raw=r.read()
            return json.loads(raw),hashlib.sha256(raw).hexdigest()
        except HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==3:raise
            time.sleep(min(10,2**attempt))

def collect(output,catalog_path=None,asset_manifest=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if asset_manifest:
        assets=sorted(json.loads(Path(asset_manifest).read_text())["requested_asset_ids"])
    else:
        if catalog_path and Path(catalog_path).exists():catalog=json.loads(Path(catalog_path).read_text())
        else:catalog,_=fetch("https://community-api.coinmetrics.io/v4/catalog/assets")
        assets=sorted(a["asset"] for a in catalog["data"] if any(m.get("metric")=="PriceUSD" for m in a.get("metrics",[])))
    batches=[assets[i:i+5] for i in range(0,len(assets),5)]
    dbpath=output/"daily_market_history.sqlite3"
    if dbpath.exists():raise FileExistsError("Refuse to overwrite prior source history")
    db=sqlite3.connect(dbpath)
    db.executescript("CREATE TABLE observations(asset TEXT,ts INTEGER,price_text TEXT,volume_text TEXT,PRIMARY KEY(asset,ts)); CREATE TABLE requests(id INTEGER PRIMARY KEY,url TEXT,response_sha256 TEXT,status TEXT,payload_json TEXT); CREATE TABLE metadata(key TEXT PRIMARY KEY,payload_json TEXT);")
    base="https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?"
    def read_batch(names):
        url=base+urlencode({"assets":",".join(names),"metrics":"PriceUSD,volume_reported_spot_usd_1d","frequency":"1d","start_time":"2021-10-24","end_time":"2026-10-24","page_size":10000})
        records=[];receipts=[]
        try:
            while url:
                value,sha=fetch(url)
                receipts.append({"url":url,"response_sha256":sha,"status":"COLLECTED","assets_requested":names,"rows":len(value.get("data",[]))})
                records.extend(value.get("data",[]));url=value.get("next_page_url")
            return names,records,receipts
        except Exception as e:
            receipts.append({"url":url,"response_sha256":None,"status":"UNAVAILABLE","assets_requested":names,"error_type":type(e).__name__,"detail":str(e)[:200]})
            return names,records,receipts
    attempted=0;request_id=0
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks=[pool.submit(read_batch,b) for b in batches]
        for future in as_completed(tasks):
            names,records,receipts=future.result();attempted+=len(names)
            for r in records:
                ts=int(datetime.fromisoformat(r["time"].replace("Z","+00:00")).timestamp())
                db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)",(r["asset"],ts,r.get("PriceUSD"),r.get("volume_reported_spot_usd_1d")))
            for r in receipts:
                db.execute("INSERT INTO requests VALUES (?,?,?,?,?)",(request_id,r["url"],r["response_sha256"],r["status"],json.dumps(r,sort_keys=True)));request_id+=1
            db.commit()
            if attempted%50==0 or attempted==len(assets):
                print(json.dumps({"event":"fresh_price_collection","assets_attempted":attempted,"assets_requested":len(assets),"records":db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],"unavailable_requests":db.execute("SELECT COUNT(*) FROM requests WHERE status!='COLLECTED'").fetchone()[0]}),flush=True)
    counts={r[0]:r[1] for r in db.execute("SELECT asset,COUNT(*) FROM observations WHERE price_text IS NOT NULL GROUP BY asset")}
    semantic=hashlib.sha256()
    for row in db.execute("SELECT asset,ts,price_text,volume_text FROM observations ORDER BY asset,ts"):
        semantic.update((json.dumps(list(row),separators=(",",":"))+"\n").encode())
    manifest={"schema":"aiscend.causal_market_data.v1","provider":"Coin Metrics Community API","attribution":"Coin Metrics, Inc","license":"CC-BY-NC-4.0","license_url":"https://creativecommons.org/licenses/by-nc/4.0/","source_api":"https://community-api.coinmetrics.io/v4","observed_at":datetime.now(timezone.utc).isoformat(),"window_start":"2021-10-24","window_end_exclusive":"2026-10-24","assets_requested":len(assets),"assets_with_price":len(counts),"price_records":sum(counts.values()),"total_metric_rows":db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],"unavailable_requests":db.execute("SELECT COUNT(*) FROM requests WHERE status!='COLLECTED'").fetchone()[0],"asset_price_counts":counts,"point_in_time":False,"notes":"Current-vintage historical reference prices; first-seen past values and executable quotes are not asserted."}
    manifest["requested_asset_ids"]=assets
    manifest["no_price_data_assets"]=sorted(set(assets)-set(counts))
    manifest["semantic_observations_sha256"]=semantic.hexdigest()
    db.execute("INSERT INTO metadata VALUES (?,?)",("manifest",json.dumps(manifest,sort_keys=True)));db.commit();db.close()
    manifest["database_sha256"]=hashlib.sha256(dbpath.read_bytes()).hexdigest()
    with dbpath.open("rb") as src,gzip.open(str(dbpath)+".gz","wb") as dst:
        import shutil
        shutil.copyfileobj(src,dst)
    manifest["compressed_database_sha256"]=hashlib.sha256(Path(str(dbpath)+".gz").read_bytes()).hexdigest()
    (output/"market_history_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"event":"fresh_price_collection_complete",**{k:v for k,v in manifest.items() if k!="asset_price_counts"}}),flush=True)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",type=Path,required=True);p.add_argument("--catalog",type=Path);p.add_argument("--asset-manifest",type=Path)
    a=p.parse_args();collect(a.output,a.catalog,a.asset_manifest)
