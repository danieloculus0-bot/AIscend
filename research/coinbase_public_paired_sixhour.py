"""Read ONLY public BTC/ETH Coinbase Exchange six-hour OHLCV, fail closed on gaps.

No account queries, order endpoints, private credentials, or current open candles.
This output is retrospective data, never proof of future trading profitability.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BAR=21600
CHUNK=240  # Smaller than Coinbase's 300-candle endpoint limit
PRODUCTS=("BTC-USD","ETH-USD")
START=int(datetime(2024,1,1,tzinfo=timezone.utc).timestamp())
END=int(datetime(2026,11,1,tzinfo=timezone.utc).timestamp())
SOURCE="https://api.exchange.coinbase.com/products/{product}/candles"

def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(",",":"),allow_nan=False)

def digest(x):
    return hashlib.sha256(canonical(x).encode("utf8")).hexdigest()

def public_fetch(url):
    req=Request(url,headers={"User-Agent":"AIscendPublicCandleResearch/1.0","Accept":"application/json"})
    with urlopen(req,timeout=25) as res:
        if res.status!=200:raise ValueError("PUBLIC_SOURCE_HTTP_FAILURE")
        return json.loads(res.read(5_000_000))

def request_url(product,start,end):
    if product not in PRODUCTS:raise ValueError("UNREGISTERED_PRODUCT")
    def iso(t):
        return datetime.fromtimestamp(t,timezone.utc).isoformat().replace("+00:00","Z")
    query=urlencode({"start":iso(start),"end":iso(end),"granularity":BAR})
    return SOURCE.format(product=product)+"?"+query

def snapshot(*,start=START,end=END,fetcher=public_fetch,sleep=time.sleep,now=time.time):
    if (type(start) is not int or type(end) is not int or start<START or end>END
            or start>=end or start%BAR or end%BAR):
        raise ValueError("INVALID_FROZEN_RANGE")
    if end>int(now()//BAR)*BAR:raise ValueError("OPEN_CANDLE_NOT_ALLOWED")
    collected={}; requests=[]
    for product in PRODUCTS:
        candles={}
        for left in range(start,end,CHUNK*BAR):
            right=min(left+CHUNK*BAR,end)
            response=fetcher(request_url(product,left,right))
            if not isinstance(response,list):raise ValueError("INVALID_PUBLIC_RESPONSE")
            requests.append({"product":product,"start":left,"end":right,
                             "raw_response_sha256":digest(response),"count":len(response)})
            # Coinbase may include a candle at an adjacent request boundary.
            # Preserve its raw response hash, but never invent it as an in-range row.
            extras=0
            for bar in response:
                if not isinstance(bar,list) or len(bar)!=6:raise ValueError("INVALID_PUBLIC_BAR")
                ts,low,high,opening,closing,volume=bar
                if type(ts) is not int or ts%BAR:
                    raise ValueError("INVALID_TIMESTAMP_OR_CHUNK_BOUNDARY")
                if not left<=ts<right:
                    if not left-BAR<=ts<=right:
                        raise ValueError("PUBLIC_RESPONSE_OUTSIDE_ADJACENT_BOUNDARY")
                    extras+=1
                    continue
                if any(type(v) not in (int,float) or not math.isfinite(v)
                       for v in (low,high,opening,closing,volume)):
                    raise ValueError("NONFINITE_PUBLIC_BAR")
                if (min(low,opening,closing,volume)<=0 or
                        not low<=min(opening,closing)<=max(opening,closing)<=high):
                    raise ValueError("INVALID_PRICE_OR_VOLUME")
                if ts in candles:raise ValueError("DUPLICATE_PUBLIC_CANDLE")
                candles[ts]={"symbol":product,"open_time":ts,"open":float(opening),
                             "high":float(high),"low":float(low),
                             "close":float(closing),"volume":float(volume)}
            requests[-1]["adjacent_boundary_rows_ignored"]=extras
            sleep(.3)
        if set(candles)!=set(range(start,end,BAR)):raise ValueError("MISSING_PUBLIC_CANDLE")
        collected[product]=candles
    times=range(start,end,BAR)
    rows=[collected[s][t] for t in times for s in PRODUCTS]
    manifest={"schema":"aiscend.public_coinbase.sixhour.snapshot.v1","source":SOURCE,
              "start":start,"end_exclusive":end,"granularity":BAR,
              "products":list(PRODUCTS),"bars_per_product":len(range(start,end,BAR)),
              "requests":requests,"rows_sha256":digest(rows),"row_count":len(rows),
              "public_only":True,"tradeable_fill_verified":False,
              "prospective_profits_verified":False}
    manifest["manifest_sha256"]=digest(manifest)
    return rows,manifest

def write_snapshot(directory,rows,manifest):
    dest=Path(directory)
    if dest.exists():raise FileExistsError("EXISTING_EVIDENCE_MUST_NOT_BE_REPLACED")
    if manifest.get("rows_sha256")!=digest(rows):raise ValueError("SOURCE_TAMPER")
    dest.mkdir(parents=True)
    (dest/"public_btc_eth.jsonl").write_text("".join(canonical(r)+"\n" for r in rows))
    (dest/"source_manifest.json").write_text(canonical(manifest)+"\n")
    return dest

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    rows,manifest=snapshot()
    write_snapshot(args.output,rows,manifest)
    print(canonical({"rows_sha256":manifest["rows_sha256"],
                     "row_count":manifest["row_count"],"public_only":True}))

if __name__=="__main__":main()
