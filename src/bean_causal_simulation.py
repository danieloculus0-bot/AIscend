"""A causal, fee-inclusive portfolio experiment using real BEAN Core learners.

Reference-price research simulation, not exchange fills. The policy runs in a
separate process and never receives future prices. Learned policy selection is
locked before the final historical interval is opened.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from .strategy_atlas import Atlas,canonical,digest,utc_seconds,iso_time

DAY=86400
ACTIONS=("cash","hold","buy_hold_btc","equal_weight","sma_cross","ema_cross","price_sma","macd","roc","vol_scaled_tsm","rsi_reversion","rsi2","bollinger_revert","zscore_revert","streak_reversal","relative_strength_rotation","inverse_vol","lowvol_factor","bollinger_breakout","volume_breakout")
VECTOR_SIZE=28
STABLE={"usdt","usdc","dai","busd","tusd","usdp","usdd","frax","lusd","susd","gusd","pyusd","usde","usds","usd1","eurc","eurt"}

@dataclass
class Panel:
    assets:tuple
    times:np.ndarray
    prices:np.ndarray
    volumes:np.ndarray
    source_sha256:str

def load_panel(database,cache,supplement=None):
    database=Path(database);cache=Path(cache)
    source_hash=hashlib.sha256(database.read_bytes()).hexdigest()
    if supplement:source_hash=digest({"atlas":source_hash,"supplement":hashlib.sha256(Path(supplement).read_bytes()).hexdigest()})
    if cache.exists():
        z=np.load(cache,allow_pickle=False)
        if str(z["source_sha256"])==source_hash:
            return Panel(tuple(str(x) for x in z["assets"]),z["times"],z["prices"],z["volumes"],source_hash)
    with Atlas(database) as atlas:
        rows=atlas.con.execute("SELECT asset,first_time,last_time,price_metrics_json FROM history_chunks WHERE price_metrics_json!='[]' ORDER BY asset").fetchall()
        first=min(int(utc_seconds(r[1])) for r in rows);last=max(int(utc_seconds(r[2])) for r in rows)
        times=np.arange(first,last+DAY,DAY,dtype=np.int64)
        prices=np.full((len(times),len(rows)),np.nan);volumes=np.full_like(prices,np.nan)
        assets=[];metric_counts=Counter()
        for col,row in enumerate(rows):
            asset=row[0];assets.append(asset)
            # Choose a named source metric once per asset, never fill gaps by
            # switching silently between different price definitions.
            metrics=json.loads(row[3]);metric="PriceUSD" if "PriceUSD" in metrics else "ReferenceRateUSD"
            metric_counts[metric]+=1
            for item in atlas.history(asset):
                i=(int(utc_seconds(item["time"]))-first)//DAY
                try:
                    value=float(item["values"][metric])
                    if math.isfinite(value) and value>0:prices[i,col]=value
                except (KeyError,ValueError,TypeError):pass
                try:
                    value=float(item["values"]["volume_reported_spot_usd_1d"])
                    if math.isfinite(value) and value>=0:volumes[i,col]=value
                except (KeyError,ValueError,TypeError):pass
    if supplement:
        with sqlite3.connect(supplement) as source:
            fresh_assets=[r[0] for r in source.execute("SELECT DISTINCT asset FROM observations WHERE price_text IS NOT NULL ORDER BY asset")]
            all_assets=sorted(set(assets)|set(fresh_assets))
            end=source.execute("SELECT MAX(ts) FROM observations WHERE asset='btc' AND price_text IS NOT NULL").fetchone()[0]
            first=min(int(times[0]),source.execute("SELECT MIN(ts) FROM observations WHERE price_text IS NOT NULL").fetchone()[0])
            merged_times=np.arange(first,end+DAY,DAY,dtype=np.int64)
            merged=np.full((len(merged_times),len(all_assets)),np.nan);merged_vol=np.full_like(merged,np.nan)
            ix={a:j for j,a in enumerate(all_assets)};fresh_set=set(fresh_assets)
            for j,a in enumerate(assets):
                if a in fresh_set:continue
                for i,t in enumerate(times):
                    k=(int(t)-first)//DAY
                    if 0<=k<len(merged_times):merged[k,ix[a]]=prices[i,j];merged_vol[k,ix[a]]=volumes[i,j]
            for asset,t,price,volume in source.execute("SELECT asset,ts,price_text,volume_text FROM observations ORDER BY asset,ts"):
                if asset not in fresh_set:continue
                i=(t-first)//DAY;j=ix[asset]
                if not 0<=i<len(merged_times):continue
                for value,array in ((price,merged),(volume,merged_vol)):
                    try:
                        x=float(value)
                        if math.isfinite(x) and (x>0 if array is merged else x>=0):array[i,j]=x
                    except (ValueError,TypeError):pass
            assets=all_assets;times=merged_times;prices=merged;volumes=merged_vol
    cache.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(cache,assets=np.array(assets),times=times,prices=prices,volumes=volumes,source_sha256=np.array(source_hash))
    print(canonical({"event":"panel_loaded","assets":len(assets),"daily_points":int(np.isfinite(prices).sum()),"metric_counts":dict(metric_counts)}),flush=True)
    return Panel(tuple(assets),times,prices,volumes,source_hash)

class Signals:
    """Trailing-only computations. Suffix mutation must leave prefixes unchanged."""
    def __init__(self,panel):
        self.p=panel
        frame=pd.DataFrame(panel.prices)
        self.sma={n:frame.rolling(n,min_periods=n).mean().to_numpy() for n in (20,50,100,200)}
        self.ema={n:frame.ewm(span=n,adjust=False,min_periods=n,ignore_na=False).mean().to_numpy() for n in (12,26,48)}
        self.macd=self.ema[12]-self.ema[26]
        self.macd_signal=pd.DataFrame(self.macd).ewm(span=9,adjust=False,min_periods=9).mean().to_numpy()
        returns=frame.pct_change(fill_method=None)
        self.returns=returns.to_numpy()
        self.vol=returns.rolling(30,min_periods=25).std(ddof=0).to_numpy()
        self.std=frame.rolling(20,min_periods=20).std(ddof=0).to_numpy()
        delta=frame.diff();gains=delta.clip(lower=0);losses=-delta.clip(upper=0)
        self.rsi={}
        for n in (2,14):
            up=gains.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
            down=losses.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
            value=100-100/(1+up/down.replace(0,np.nan))
            value=value.mask((down==0)&(up>0),100).mask((down==0)&(up==0),50)
            self.rsi[n]=value.to_numpy()
        vf=pd.DataFrame(panel.volumes)
        self.liquidity=vf.rolling(20,min_periods=15).mean().to_numpy()
        self.relative_volume=(vf/vf.shift(1).rolling(20,min_periods=15).mean()).to_numpy()
        self.valid90=frame.notna().rolling(90,min_periods=90).sum().to_numpy()>=85
        self.stable=np.array([a in STABLE for a in panel.assets])

    def change(self,i,n):
        if i<n:return np.full(len(self.p.assets),np.nan)
        with np.errstate(invalid="ignore",divide="ignore"):return self.p.prices[i]/self.p.prices[i-n]-1

    def eligible(self,i):
        return self.valid90[i]&np.isfinite(self.p.prices[i])&np.isfinite(self.liquidity[i])&(self.liquidity[i]>=100_000)&~self.stable

    def proposals(self,i,positions):
        p=self.p.prices[i];valid=self.eligible(i);liq=self.liquidity[i]
        held=np.array([a in positions and positions[a]>0 for a in self.p.assets])
        universe=np.flatnonzero(valid)
        universe=sorted(universe,key=lambda j:(-liq[j],self.p.assets[j]))[:100]
        u=np.zeros(len(p),dtype=bool);u[universe]=True
        r20=self.change(i,20);r30=self.change(i,30);r90=self.change(i,90)
        mean=self.sma[20][i];std=self.std[i];vol=self.vol[i]
        z=np.divide(p-mean,std,out=np.full_like(p,np.nan),where=std>0)
        flags={"equal_weight":u,"sma_cross":u&(self.sma[20][i]>self.sma[100][i]),
               "ema_cross":u&(self.ema[12][i]>self.ema[48][i]),
               "price_sma":u&(p>self.sma[200][i]),
               "macd":u&(self.macd[i]>self.macd_signal[i])&((self.macd[i]>0)|held),
               "roc":u&(r20>.03),"vol_scaled_tsm":u&(r90>0),
               "rsi_reversion":u&((self.rsi[14][i]<30)|(held&(self.rsi[14][i]<50))),
               "rsi2":u&(p>self.sma[200][i])&((self.rsi[2][i]<5)|(held&(self.rsi[2][i]<=70))),
               "bollinger_revert":u&((p<mean-2*std)|(held&(p<mean))),
               "zscore_revert":u&((z < -2)|(held&(z<0))),
               "relative_strength_rotation":u&(r30>0),"inverse_vol":u,
               "lowvol_factor":u,"bollinger_breakout":u&(((p>mean+2*std)&(self.relative_volume[i]>1.5))|(held&(p>=mean))),
               "volume_breakout":u&(((self.relative_volume[i]>2)&(self.returns[i]>.02))|(held&(self.returns[i]>=0)))}
        down=np.ones(len(p),bool);up=np.ones(len(p),bool)
        for k in range(4):down &= self.returns[i-k]<0
        for k in range(2):up &= self.returns[i-k]>0
        flags["streak_reversal"]=u&(down|(held&~up))
        out={"cash":{},"hold":None}
        btc=self.p.assets.index("btc") if "btc" in self.p.assets else None
        out["buy_hold_btc"]={"btc":1.0} if btc is not None and np.isfinite(p[btc]) else {}
        for name,flag in flags.items():
            indices=np.flatnonzero(flag)
            if name in ("rsi_reversion","rsi2"):score=-self.rsi[2 if name=="rsi2" else 14][i]
            elif name in ("bollinger_revert","zscore_revert"):score=-z
            elif name in ("inverse_vol","lowvol_factor"):score=-vol
            elif name in ("equal_weight",):score=liq
            else:score=r30
            chosen=sorted((j for j in indices if np.isfinite(score[j])),key=lambda j:(-score[j],self.p.assets[j]))[:5]
            if name in ("inverse_vol","vol_scaled_tsm"):
                inv=np.array([1/max(float(vol[j]),.005) for j in chosen]);den=float(inv.sum())
                weights={self.p.assets[j]:min(.2,float(v)/den) for j,v in zip(chosen,inv)} if den else {}
            else:weights={self.p.assets[j]:.2 for j in chosen}
            out[name]=weights
        if set(out)!=set(ACTIONS):raise AssertionError("Unimplemented registered action")
        return out

    def observation(self,i,broker):
        valid=self.eligible(i);n=int(valid.sum());p=self.p.prices[i]
        def med(a):
            x=a[valid];x=x[np.isfinite(x)]
            return float(np.median(x)) if len(x) else 0.0
        changes=[self.change(i,n) for n in (1,7,30,90)]
        vec=[med(a)/s for a,s in zip(changes,(.03,.10,.25,.50))]
        vec += [float(np.mean(p[valid]>self.sma[n][i][valid])) if valid.any() else 0 for n in (20,50,100)]
        vec += [med(self.vol[i])/.05, min(n/500,2)]
        cross=changes[1][valid];cross=cross[np.isfinite(cross)]
        vec.append(float(np.std(cross))/.1 if len(cross) else 0)
        btc=self.p.assets.index("btc") if "btc" in self.p.assets else None
        for x,s in zip(changes[1:],(.10,.25,.50)):vec.append(float(x[btc])/s if btc is not None and np.isfinite(x[btc]) else 0)
        equity=broker.value(i)
        vec += [broker.cash/max(equity,1e-9),math.log(max(equity,1e-9)/broker.initial),1-equity/max(broker.peak,1e-9),len(broker.positions)/5,broker.costs/broker.initial]
        recent=broker.rewards[-5:]
        vec += [0.]*(5-len(recent))+[r/.05 for r in recent]
        vec += [broker.age/30,broker.last_turnover,broker.orders/100,broker.gap_marks/100,math.log1p(max(equity,0))/10]
        if len(vec)!=VECTOR_SIZE:raise AssertionError(len(vec))
        vector=[float(np.clip(x,-5,5)) for x in vec]
        bins=[int(np.digitize(vec[k],[-.2,.2])) for k in (0,2,5,7,14)]+[int(broker.cash/max(equity,1e-9)>.5)]
        return {"vector":vector,"bins":bins,"eligible_assets":n,"latest_input_time":int(self.p.times[i]),"available_at":int(self.p.times[i])+DAY,
                "account":{"starting_equity":broker.initial,"equity":equity,"cash":broker.cash,"positions":dict(broker.positions),"paid_costs":broker.costs,"recent_matured_returns":list(recent)}}

class Broker:
    def __init__(self,panel,initial=1000.,fee_bps=90.,half_spread_bps=5.,slippage_bps=5.):
        self.panel=panel;self.initial=float(initial);self.cash=float(initial);self.positions={};self.cost=(fee_bps+half_spread_bps+slippage_bps)/10000
        if not 0<=self.cost<1:raise ValueError("Invalid costs")
        self.costs=0.;self.orders=0;self.peak=self.initial;self.max_drawdown=0.;self.gap_marks=0;self.rewards=[];self.age=0;self.last_turnover=0.;self.fills=[]
        self.index={a:i for i,a in enumerate(panel.assets)}
    def price(self,asset,i):
        j=self.index[asset];v=float(self.panel.prices[i,j])
        return v if math.isfinite(v) and v>0 else None
    def value(self,i):
        # Missing reference observations are conservatively marked zero, never
        # dropped with their losses or interpolated across an outage.
        return self.cash+sum(q*(self.price(a,i) or 0.) for a,q in self.positions.items())
    def mark(self,i):
        missing=sum(self.price(a,i) is None for a in self.positions)
        self.gap_marks+=missing
        mark=self.value(i)-sum(q*(self.price(a,i) or 0.)*self.cost for a,q in self.positions.items())
        self.peak=max(self.peak,mark);self.max_drawdown=max(self.max_drawdown,1-mark/max(self.peak,1e-9))
        return mark
    def trade(self,weights,decision_i,fill_i,liquidity):
        self.fills=[];turnover=0.
        if weights is None:self.age+=2;self.last_turnover=0.;return
        if any(not math.isfinite(w) or w<0 for w in weights.values()) or sum(weights.values())>1.000001:raise ValueError("Unfunded target")
        equity=self.value(fill_i)
        desired={a:equity*w for a,w in weights.items()}
        for side in ("sell","buy"):
            assets=sorted(set(self.positions)|set(desired))
            for a in assets:
                p=self.price(a,fill_i)
                if p is None:continue  # actual attempted order is unfilled
                old=self.positions.get(a,0.)*p;diff=desired.get(a,0.)-old
                if (side=="sell" and diff>=-1) or (side=="buy" and diff<=1):continue
                known=float(liquidity[self.index[a]])
                max_notional=.01*known if math.isfinite(known) and known>=0 else 0.
                amount=min(abs(diff),max_notional)
                if side=="buy":amount=min(amount,self.cash/(1+self.cost))
                else:amount=min(amount,old)
                if amount<1:continue
                fee=amount*self.cost;qty=amount/p
                if side=="sell":qty=-qty;self.cash+=amount-fee
                else:self.cash-=amount+fee
                self.positions[a]=self.positions.get(a,0.)+qty
                if self.positions[a]<1e-12:self.positions.pop(a,None)
                if self.cash < -1e-7:raise AssertionError("Negative cash")
                self.costs+=fee;self.orders+=1;turnover+=amount
                self.fills.append({"asset":a,"side":side,"quantity":qty,"reference_price":p,"notional":amount,"modeled_cost":fee,"decision_price_time":int(self.panel.times[decision_i]),"execution_price_time":int(self.panel.times[fill_i])})
        self.last_turnover=turnover/max(equity,1e-9);self.age=0
    def liquidate(self,i):
        for a,q in list(self.positions.items()):
            p=self.price(a,i)
            if p is not None:
                gross=q*p;fee=gross*self.cost;self.cash+=gross-fee;self.costs+=fee;self.orders+=1
            # A terminal missing asset is written down, not priced with a future quote.
            else:self.gap_marks+=1
        self.positions.clear();self.mark(i)

class Worker:
    def __init__(self,core_dir,journal,engine,seed,*,restore=False):
        env={k:v for k,v in os.environ.items() if k in ("PATH","PYTHONPATH","LD_LIBRARY_PATH","LANG","SYSTEMROOT","WINDIR")}
        self.proc=subprocess.Popen([sys.executable,"-m","src.bean_policy_worker"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,env=env)
        self.call("init",core_dir=str(Path(core_dir).resolve()),journal=str(Path(journal).resolve()),engine=engine,seed=seed,size=VECTOR_SIZE,actions=list(ACTIONS),restore=restore)
    def call(self,op,**values):
        self.proc.stdin.write(canonical({"op":op,**values})+"\n");self.proc.stdin.flush()
        line=self.proc.stdout.readline()
        if not line:
            error=self.proc.stderr.read();raise RuntimeError("BEAN worker stopped: "+error[-3000:])
        result=json.loads(line)
        if not result["ok"]:raise RuntimeError(result["error"])
        return result["result"]
    def close(self):
        if self.proc.poll() is None:
            try:self.call("close")
            finally:self.proc.wait(timeout=15)
        for stream in (self.proc.stdin,self.proc.stdout,self.proc.stderr):
            if stream:stream.close()

class DecisionLedger:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream_path=self.path.with_suffix(".jsonl")
        self.metadata_path=self.path.with_suffix(".metadata.json")
        self.db=sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE IF NOT EXISTS decisions(seq INTEGER PRIMARY KEY,previous_hash TEXT,entry_hash TEXT UNIQUE,payload_json TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS experiment_metadata(key TEXT PRIMARY KEY,value_json TEXT)")
        self.tip="0"*64;self.seq=0
        if self.stream_path.exists():
            for line in self.stream_path.read_text().splitlines():
                item=json.loads(line);seq=item["seq"];previous=item["previous_hash"];claimed=item["entry_hash"];raw=canonical(item["payload"])
                expected=hashlib.sha256((self.tip+"\n"+raw).encode()).hexdigest()
                if seq!=self.seq or previous!=self.tip or claimed!=expected:raise ValueError("Decision ledger changed or reordered")
                self.db.execute("INSERT INTO decisions VALUES (?,?,?,?)",(seq,previous,claimed,raw));self.tip=claimed;self.seq+=1
        if self.path.exists():
            with sqlite3.connect(self.path) as prior:
                rows=prior.execute("SELECT seq,previous_hash,entry_hash,payload_json FROM decisions ORDER BY seq").fetchall()
            expected=self.db.execute("SELECT seq,previous_hash,entry_hash,payload_json FROM decisions ORDER BY seq").fetchall()
            if rows!=expected:raise ValueError("SQLite snapshot differs from authoritative event stream")
        if self.metadata_path.exists():
            for k,v in json.loads(self.metadata_path.read_text()).items():self.db.execute("INSERT INTO experiment_metadata VALUES (?,?)",(k,canonical(v)))
        self.db.commit()
        self.stream=self.stream_path.open("a",encoding="utf-8")
    def record(self,payload):
        raw=canonical(payload);h=hashlib.sha256((self.tip+"\n"+raw).encode()).hexdigest()
        # Append evidence first. File-backed SQLite is materialized atomically
        # at phase boundaries, avoiding high-frequency journal synchronization.
        self.stream.write(canonical({"seq":self.seq,"previous_hash":self.tip,"entry_hash":h,"payload":payload})+"\n")
        self.stream.flush()
        if self.seq%50==0:os.fsync(self.stream.fileno())
        self.db.execute("INSERT INTO decisions VALUES (?,?,?,?)",(self.seq,self.tip,h,raw))
        self.tip=h;self.seq+=1
    def metadata(self,key,value):
        old=self.db.execute("SELECT value_json FROM experiment_metadata WHERE key=?",(key,)).fetchone()
        if old:
            if old[0]!=canonical(value):raise ValueError("Immutable experiment metadata changed")
            return
        self.db.execute("INSERT INTO experiment_metadata VALUES (?,?)",(key,canonical(value)));self.db.commit()
        metadata={k:json.loads(v) for k,v in self.db.execute("SELECT key,value_json FROM experiment_metadata")}
        temp=self.metadata_path.with_suffix(".building")
        temp.write_text(canonical(metadata)+"\n");os.replace(temp,self.metadata_path)
    def snapshot(self):
        self.stream.flush();os.fsync(self.stream.fileno());self.db.commit()
        temp=self.path.with_suffix(".building.sqlite3")
        temp.unlink(missing_ok=True)
        target=sqlite3.connect(temp)
        self.db.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Snapshot integrity failed")
        target.close();os.replace(temp,self.path)
    def close(self):self.snapshot();self.stream.close();self.db.close()

def replay(panel,signals,worker,start,end,*,label,ledger,training=False,placebo=False,fixed=None,seed=0):
    broker=Broker(panel);rng=random.Random(seed);past_rewards=[];choices=Counter();start_status=worker.call("status") if worker else None
    observations=[]
    for i in range(start,end-2,2):
        obs=signals.observation(i,broker)
        proposals=signals.proposals(i,broker.positions)
        if worker:
            chosen=worker.call("choose",vector=obs["vector"],bins=obs["bins"],epsilon=max(.05,.35*(end-i)/(end-start)) if training else 0,training=training)
            action=ACTIONS[chosen["action"]]
        else:action=fixed
        choices[action]+=1
        before=broker.value(i)
        broker.trade(proposals[action],i,i+1,signals.liquidity[i])
        broker.mark(i+1);broker.mark(i+2)
        done=i+4>=end
        if done:broker.liquidate(i+2)
        after=broker.value(i+2);real_return=after/max(before,1e-9)-1
        reward=100*math.log(max(after,1e-9)/max(before,1e-9))
        broker.rewards.append(real_return)
        following=signals.observation(i+2,broker)
        learned=None
        if worker and training:
            used=rng.choice(past_rewards) if placebo and past_rewards else (0. if placebo else reward)
            past_rewards.append(reward)
            learned=worker.call("observe",vector=obs["vector"],bins=obs["bins"],action=ACTIONS.index(action),reward=used,next_vector=following["vector"],next_bins=following["bins"],done=done,decision_at=obs["available_at"],matured_at=int(panel.times[i+2])+DAY,extra_updates=3)
        payload={"phase":label,"decision_index":i,"decision_at":obs["available_at"],"max_input_price_time":obs["latest_input_time"],"earliest_execution_price_time":int(panel.times[i+1]),"execution_at":int(panel.times[i+1])+DAY,"outcome_available_at":int(panel.times[i+2])+DAY,
                 "observation":obs,"observation_sha256":digest(obs),"action":action,"target_weights":proposals[action],"fills":broker.fills,"equity_before":before,"equity_after":after,"cash_after":broker.cash,"modeled_cumulative_costs":broker.costs,"realized_period_return":real_return,"reward_after_maturity":reward,"learning":learned,"live_orders":False}
        ledger.record(payload);observations.append(payload["observation_sha256"])
    if broker.positions:broker.liquidate(min(end-1,len(panel.times)-1))
    final_status=worker.call("status") if worker else None
    if worker and not training and final_status["weights_sha256"]!=start_status["weights_sha256"]:raise AssertionError("Frozen evaluation changed learned weights")
    result={"phase":label,"starting_equity":broker.initial,"ending_equity":broker.cash,"net_return":broker.cash/broker.initial-1,"max_drawdown":broker.max_drawdown,"modeled_costs":broker.costs,"orders":broker.orders,"decisions":sum(choices.values()),"action_counts":dict(choices),"gap_marks":broker.gap_marks,"first_decision":int(panel.times[start])+DAY,"last_data_time":int(panel.times[min(end-1,len(panel.times)-1)]),"initial_learner":start_status,"final_learner":final_status,"observation_chain_sha256":digest(observations)}
    ledger.snapshot()
    print(canonical({"event":"phase_complete","phase":label,"equity":round(broker.cash,4),"orders":broker.orders,"learner_updates":final_status.get("updates") if final_status else None}),flush=True)
    return result

def select_candidate(candidates):
    if not candidates or any(not math.isfinite(c["validation"]["net_return"]) for c in candidates):raise ValueError("Complete finite validation required")
    return sorted(candidates,key=lambda c:(-c["validation"]["net_return"],c["id"]))[0]

def run(database,core_dir,output,*,seeds=(7,41,20261024),minimum_history=200,supplement=None):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError("Refuse to overwrite a prior causal experiment")
    output.mkdir(parents=True,exist_ok=True)
    panel=load_panel(database,output.parent/("fresh-atlas-price-panel.npz" if supplement else "atlas-price-panel.npz"),supplement)
    n=len(panel.times);start=minimum_history;train_end=int(n*.60);validation_end=int(n*.80)
    core_revision=subprocess.check_output(["git","-C",str(core_dir),"rev-parse","HEAD"],text=True).strip()
    core_source_hash=digest({p:hashlib.sha256((Path(core_dir)/p).read_bytes()).hexdigest() for p in ("bean/optimization/sponge_dqn.py","bean/optimization/sponge_td.py","bean/learning/runtime.py")})
    protocol={"schema":"aiscend.bean.causal_protocol.v1","source_database_sha256":panel.source_sha256,"core_revision":core_revision,"core_source_sha256":core_source_hash,"assets_considered":len(panel.assets),"asset_ids":list(panel.assets),"actions":list(ACTIONS),"seeds":list(seeds),"training_indices":[start,train_end],"validation_indices":[train_end,validation_end],"holdout_indices":[validation_end,n],"chronology":"features through i; hypothetical fill at i+1; outcome at i+2; feedback only after outcome; step2 days","costs":{"fee_bps_per_side":90,"half_spread_bps":5,"slippage_bps":5},"initial_capital":1000,"selection":"best validation net return among genuine BEAN candidates, fixed tie by candidate id; validation cannot retrain; no final-period reselection","holdout_arms":["selected_frozen","selected_online_matured_feedback","same_architecture_untrained","past_reward_placebo","cash","buy_hold_btc","relative_strength_rotation"],"successful_method_rule":"selected_frozen positive net, beats cash/BTC/fixed momentum and its untrained/placebo controls; max drawdown <=25%; positive learned effect must survive controls","snapshot_scope":"current-vintage reference-price simulation; not exchange quotes/fills; all asset histories considered, price/volume warmup decides eligibility using past only","narrative_policy":"No historical first-seen news in this window. Today's RSS is never backdated into this experiment."}
    protocol["simulator_source_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    protocol["policy_worker_sha256"]=hashlib.sha256(Path(__file__).with_name("bean_policy_worker.py").read_bytes()).hexdigest()
    protocol["protocol_sha256"]=digest(protocol)
    (output/"protocol.json").write_text(json.dumps(protocol,indent=2)+"\n")
    # Written before any training or final-period portfolio outcome is calculated.
    signals=Signals(panel);ledger=DecisionLedger(output/"decisions.sqlite3");ledger.metadata("protocol",protocol)
    candidates=[]
    for engine,seed in [("td",seeds[0])]+[("dqn",s) for s in seeds]:
        label=f"{engine}-{seed}";journal=output/(label+".sqlite3")
        worker=Worker(core_dir,journal,engine,seed)
        initial=worker.call("status")
        train=replay(panel,signals,worker,start,train_end,label=label+"/train",ledger=ledger,training=True,seed=seed)
        trained=worker.call("status")
        validation=replay(panel,signals,worker,train_end,validation_end,label=label+"/validation",ledger=ledger)
        checkpoint=worker.call("checkpoint",baseline=0.,candidate=validation["net_return"],samples=validation["decisions"],evaluation_sha256=digest(validation),episode="validation-only",metadata={"protocol_sha256":protocol["protocol_sha256"],"training_until":int(panel.times[train_end-1]),"validation_until":int(panel.times[validation_end-1]),"no_holdout_observed":True})
        probe=signals.observation(validation_end-1,Broker(panel))
        action_before=worker.call("choose",vector=probe["vector"],bins=probe["bins"])
        worker.close()
        restored=Worker(core_dir,journal,engine,seed,restore=True)
        restored_status=restored.call("status");action_after=restored.call("choose",vector=probe["vector"],bins=probe["bins"])
        restart_ok=(restored_status["weights_sha256"]==trained["weights_sha256"] and restored_status["updates"]==trained["updates"] and action_before==action_after)
        restored.close()
        if not restart_ok:raise AssertionError("Learned policy failed restart/decision recovery")
        candidates.append({"id":label,"engine":engine,"seed":seed,"journal":journal.name,"training":train,"validation":validation,"checkpoint":checkpoint,"weights_changed_from_training":trained["weights_sha256"]!=initial["weights_sha256"],"retention_after_restart":restart_ok})
        (output/"development.json").write_text(json.dumps(candidates,indent=2)+"\n")
    selected=select_candidate(candidates)
    lock={"schema":"aiscend.bean.selection_lock.v1","protocol_sha256":protocol["protocol_sha256"],"selected_id":selected["id"],"selected_validation_return":selected["validation"]["net_return"],"checkpoint_weights_sha256":selected["validation"]["final_learner"]["weights_sha256"],"all_development_sha256":digest(candidates),"holdout_opened":False}
    (output/"selection_lock.json").write_text(json.dumps(lock,indent=2)+"\n");ledger.metadata("selection_lock",lock)
    print(canonical({"event":"selection_locked","selected":selected["id"],"validation_return":lock["selected_validation_return"]}),flush=True)
    results={}
    engine=selected["engine"];seed=selected["seed"];saved=output/selected["journal"]
    import shutil
    for mode in ("selected_frozen","selected_online_matured_feedback"):
        target=output/(mode+".sqlite3");shutil.copyfile(saved,target)
        worker=Worker(core_dir,target,engine,seed,restore=True)
        results[mode]=replay(panel,signals,worker,validation_end,n,label=mode,ledger=ledger,training=mode.endswith("feedback"),seed=seed)
        if mode.endswith("feedback"):
            worker.call("checkpoint",baseline=0.,candidate=results[mode]["net_return"],samples=results[mode]["decisions"],evaluation_sha256=digest(results[mode]),episode="prospective-historical-stream",metadata={"protocol_sha256":protocol["protocol_sha256"],"holdout_outcomes_used_only_after_maturity":True})
        worker.close()
    cold=Worker(core_dir,output/"untrained.sqlite3",engine,seed)
    results["same_architecture_untrained"]=replay(panel,signals,cold,validation_end,n,label="same_architecture_untrained",ledger=ledger);cold.close()
    placebo=Worker(core_dir,output/"placebo.sqlite3",engine,seed)
    placebo_train=replay(panel,signals,placebo,start,train_end,label="placebo/train",ledger=ledger,training=True,placebo=True,seed=seed)
    results["past_reward_placebo"]=replay(panel,signals,placebo,validation_end,n,label="past_reward_placebo",ledger=ledger);placebo.close()
    for baseline in ("cash","buy_hold_btc","relative_strength_rotation"):
        results[baseline]=replay(panel,signals,None,validation_end,n,label=baseline,ledger=ledger,fixed=baseline)
    frozen=results["selected_frozen"]
    controls=("cash","buy_hold_btc","relative_strength_rotation","same_architecture_untrained","past_reward_placebo")
    success=frozen["net_return"]>0 and frozen["max_drawdown"]<=.25 and all(frozen["net_return"]>results[c]["net_return"] for c in controls)
    report={"schema":"aiscend.bean.causal_results.v1","protocol_sha256":protocol["protocol_sha256"],"source_database_sha256":panel.source_sha256,"core_revision":core_revision,"core_source_sha256":core_source_hash,"selected_candidate":selected["id"],"all_candidates_retained":len(candidates),"restart_verified":all(c["retention_after_restart"] for c in candidates),"actual_weight_or_q_changes":{c["id"]:c["weights_changed_from_training"] for c in candidates},"historical_final_period_results":results,"successful_method_under_preregistered_rule":success,"independent_external_replication":False,"live_profitability_proven":False,"live_orders":False,"decision_ledger_tip":ledger.tip,"decision_records":ledger.seq,"retained_policy":str(output/"selected_frozen.sqlite3"),"retained_online_policy":str(output/"selected_online_matured_feedback.sqlite3"),"limitations":["Historical current-vintage reference prices, not executable venue fills","Present archive coverage has survivorship and availability limits","Only the price/volume-feasible catalog subset is implemented in this experiment","No historical first-seen narrative input was available"]}
    ledger.metadata("results",report);ledger.close()
    (output/"results.json").write_text(json.dumps(report,indent=2)+"\n")
    for p in sorted(output.glob("*.sqlite3")):
        with p.open("rb") as src,gzip.open(str(p)+".gz","wb") as dest:shutil.copyfileobj(src,dest)
    print(canonical({"event":"experiment_complete","selected":selected["id"],"success":success,"results":{k:{"net_return":v["net_return"],"ending_equity":v["ending_equity"]} for k,v in results.items()},"decision_records":ledger.seq}),flush=True)
    return report

def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database",type=Path,default=Path("research/strategy_atlas/strategy_research.sqlite3.gz"))
    p.add_argument("--core-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,default=Path("research/bean_causal_retention_20261024"))
    p.add_argument("--seeds",type=int,nargs="+",default=[7,41,20261024])
    p.add_argument("--supplement",type=Path)
    a=p.parse_args(argv);run(a.database,a.core_dir,a.output,seeds=tuple(a.seeds),supplement=a.supplement)

if __name__=="__main__":main()
