"""AIscend research ONLY: causal cross-asset rotation with the REAL pinned BEAN SpongeDQN.

Three economically distinct targets (cash, strongest, runner-up) allow HOLD,
ROTATE, DEFEND, and REENTER to emerge from positions and price history.
This module NEVER imports an AIscend execution adapter or opens exchange connections.

Research note: a synthetic fixture tests machinery, NOT market profitability.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
import subprocess
import sys
from pathlib import Path

BAR = 21600  # Six-hour candles
HORIZON = 8
DELAY = 2  # Decision follows close t; fill starts at open t+2, not t+1.
LOOKBACK = 16
COST = 0.0095  # 95 basis points PER SIDE (fee + conservative spread/slippage).
PARTICIPATION = 0.01
ACTIONS = ("cash", "leader", "runner_up")
CORE_REVISION = "d13ea68fd2d0fd4cd7af1cd297e1fbec75a954fe"
CORE_BLOB = "08abcf5039f7f440fa936aba31ef9a3a2b96f153"  # sponge_dqn.py at pinned Core.
SCHEMA = "aiscend.bean.shadow.rotation.research.v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def load_bars(rows):
    """Strict aligned OHCV (plus optional H/L), exactly two public assets."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("EMPTY_SOURCE")
    by_symbol = {}
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("INVALID_BAR")
        timestamp = r.get("open_time", r.get("ts"))
        if type(timestamp) is not int or ("open_time" in r and "ts" in r and r["open_time"] != r["ts"]):
            raise ValueError("INVALID_TIMESTAMP")
        symbol = r.get("symbol")
        if not isinstance(symbol, str) or not symbol or len(symbol) > 30:
            raise ValueError("INVALID_SYMBOL")
        values = []
        for key in ("open", "close", "volume"):
            v = r.get(key)
            if type(v) not in (int, float) or not math.isfinite(v):
                raise ValueError("NONFINITE_BAR")
            values.append(float(v))
        o,c,v = values
        if timestamp % BAR or min(o,c,v) <= 0:
            raise ValueError("INVALID_BAR")
        h=l=None
        if "high" in r or "low" in r:
            if not ("high" in r and "low" in r):
                raise ValueError("INCOMPLETE_RANGE")
            h,l = r["high"],r["low"]
            if (type(h) not in (int,float) or type(l) not in (int,float) or
                not math.isfinite(h) or not math.isfinite(l) or l <= 0 or
                l > min(o,c) or h < max(o,c)):
                raise ValueError("INVALID_RANGE")
            h,l=float(h),float(l)
        if timestamp in by_symbol.setdefault(symbol, {}):
            raise ValueError("DUPLICATE_BAR")
        by_symbol[symbol][timestamp] = (o,h,l,c,v)
    symbols = sorted(by_symbol)
    if len(symbols) != 2:
        raise ValueError("TWO_ASSETS_REQUIRED")
    times = sorted(by_symbol[symbols[0]])
    if len(times) < 240 or any(y-x != BAR for x, y in zip(times, times[1:])):
        raise ValueError("INSUFFICIENT_OR_GAPPED_HISTORY")
    if set(times) != set(by_symbol[symbols[1]]):
        raise ValueError("UNALIGNED_ASSETS")
    arrays = [[by_symbol[s][t] for t in times] for s in symbols]
    return {"symbols": symbols, "times": times, "bars": arrays, "source_sha256": sha(rows)}


def close(market, asset, index):
    return market["bars"][asset][index][3]


def targets(market, i):
    # Ranking uses only historical CLOSED bars, never the entry/open at i+2.
    scores = []
    for a in range(2):
        v = math.log(close(market, a, i)) - math.log(close(market, a, i-16))
        scores.append((v, -a, a))
    ordered = sorted(scores, reverse=True)
    return (None, ordered[0][2], ordered[1][2])


def long_horizon_regime(market, i, asset):
    """200-day average gap and 365-day momentum from completed six-hour bars."""
    if i < 0 or i >= len(market["times"]) or asset not in (0,1):
        raise ValueError("INVALID_REGIME_OBSERVATION")
    gap = 0.
    momentum = 0.
    if i >= 799:
        sma = math.fsum(close(market,asset,j) for j in range(i-799,i+1))/800.
        gap = math.log(close(market,asset,i)/sma)
    if i >= 1460:
        momentum = math.log(close(market,asset,i)/close(market,asset,i-1460))
    return gap,momentum


def observation(market, i, position):
    if i < LOOKBACK or i >= len(market["times"]):
        raise ValueError("FEATURE_WINDOW")
    vector = []
    for a in range(2):
        current = math.log(close(market, a, i))
        logs = [math.log(close(market, a, j)) for j in range(i-16, i+1)]
        step = [logs[k]-logs[k-1] for k in range(1,len(logs))]
        volatility = math.sqrt(math.fsum(x*x for x in step)/len(step))
        vector.extend(((current-logs[-5])*10, (current-logs[0])*4, volatility*20))
    vector.extend((float(position is None),float(position == 0),float(position == 1)))
    # Human-investor lesson: long-horizon trend persistence and bear markets.
    # Both signals are computed ONLY from completed bars up through i.
    for a in range(2):
        gap, momentum = long_horizon_regime(market, i, a)
        vector.extend((gap*4, momentum*2))
    vector.extend((float(i>=799), float(i>=1460)))
    return [max(-5.,min(5.,float(x))) for x in vector]


def net_liquidation(cash, quantity, position, closes, cost=COST):
    value = cash if position is None else cash + quantity*closes[position]*(1-cost)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("INVALID_NAV")
    return value


def step(market, i, position, cash, quantity, action, *, cost=COST):
    """Causal executed target at i+2 open, scored only after i+H close.

    Rejected/insufficient-liquidity fill leaves original inventory unchanged;
    never invent a completed sale. Returns an auditable transition receipt.
    """
    if type(action) is not int or not 0 <= action < len(ACTIONS):
        raise ValueError("INVALID_ACTION")
    if i < LOOKBACK or i+HORIZON >= len(market["times"]):
        raise ValueError("UNMATURED_STEP")
    if not 0 <= cost < .5 or not all(math.isfinite(x) for x in (cash,quantity,cost)) or min(cash,quantity) < 0:
        raise ValueError("INVALID_ACCOUNT")
    before = net_liquidation(cash,quantity,position,[close(market,a,i) for a in range(2)],cost)
    prior_position = position
    ranks = targets(market,i)
    target = ranks[action]
    fill_at = i+DELAY
    fills = []
    rejected = None
    if target != position:
        # Preflight *both* legs atomically: do not sell an incumbent unless its
        # replacement can also be bought with modeled candle participation.
        sell = None
        if position is not None:
            sell = market["bars"][position][fill_at]
            if quantity > sell[4]*PARTICIPATION:
                rejected = "SELL_CAPACITY"
        proceeds = cash + (quantity*sell[0]*(1-cost) if sell else 0)
        candidate_qty = 0.
        if target is not None:
            buy = market["bars"][target][fill_at]
            candidate_qty = proceeds*(1-cost)/buy[0]
            if not math.isfinite(candidate_qty) or candidate_qty <= 0 or candidate_qty > buy[4]*PARTICIPATION:
                rejected = rejected or "BUY_CAPACITY"
        if not math.isfinite(proceeds) or proceeds <= 0:
            rejected = rejected or "NONFINITE_SETTLEMENT"
        if rejected is None:
            if position is not None:
                fills.append({"side":"sell","asset":position,"price":sell[0],"quantity":quantity})
            if target is not None:
                fills.append({"side":"buy","asset":target,"price":market["bars"][target][fill_at][0],"quantity":candidate_qty})
                cash, quantity, position = 0., candidate_qty, target
            else:
                cash, quantity, position = proceeds, 0., None
    after = net_liquidation(cash,quantity,position,[close(market,a,i+HORIZON) for a in range(2)],cost)
    reward = 100*math.log(after/before)
    if not math.isfinite(reward):
        raise ValueError("NONFINITE_REWARD")
    behavior = ("UNFILLED" if rejected else
                "ABSTAIN" if target == prior_position and prior_position is None else
                "HOLD" if target == prior_position else
                "DEFEND" if target is None else
                "REENTER" if prior_position is None else "ROTATE")
    event = {"signal_ts":market["times"][i]+BAR,
             "feature_sha256":sha(observation(market,i,prior_position)),
             "ranked_target_assets":list(ranks),
             "entry_ts":market["times"][fill_at],
             "matured_ts":market["times"][i+HORIZON]+BAR,
             "action":ACTIONS[action],"behavior":behavior,
             "requested_target":target,"position":position,
             "fills":fills,"unfilled_reason":rejected,
             "nav_before":before,"nav_after":after,"reward":reward}
    if not event["signal_ts"] < event["entry_ts"] < event["matured_ts"]:
        raise AssertionError("CAUSAL_TIMING")
    return position,cash,quantity,event


def rollout(market, begin, end, chooser, *, learn=None):
    position,cash,quantity = None,1000.,0.
    events=[];nav=[cash]
    for i in range(begin,end-HORIZON,HORIZON):
        before = observation(market,i,position)
        a=chooser(before,position,i)
        position,cash,quantity,event=step(market,i,position,cash,quantity,a)
        if learn is not None and event["unfilled_reason"] is None:
            # The agent receives its reward only AFTER the causal outcome matures.
            # Terminal training windows do not bootstrap into an unobserved episode.
            done = i + 2*HORIZON >= end
            learn(before,a,event["reward"],observation(market,i+HORIZON,position),done)
        events.append(event);nav.append(event["nav_after"])
    peak = nav[0]
    drawdown = 0.
    for v in nav:
        peak=max(peak,v)
        drawdown=max(drawdown,1-v/peak)
    return {"net_return":nav[-1]/nav[0]-1,"max_drawdown":drawdown,
            "periods":len(events),"unfilled":sum(bool(e["unfilled_reason"]) for e in events),
            "events":events,"event_sha256":sha(events),"final_nav":nav[-1],
            "period_log_returns":[math.log(e["nav_after"]/e["nav_before"]) for e in events]}


def fixed(market,name):
    def choose(_vector,position,i):
        ranks=targets(market,i)
        if name=="cash":return 0
        if name=="buy_hold_first":return ranks.index(0)
        if name=="leader":return 1
        if name=="trend_exit":return 1 if math.log(close(market,ranks[1],i)/close(market,ranks[1],i-16))>0 else 0
        if name=="btc_200d_12m_defensive":
            if "BTC-USD" not in market["symbols"]:
                raise ValueError("BTC_CONTROL_REQUIRES_BTC")
            if i < 1460:
                return 0  # no invented prehistory
            btc = market["symbols"].index("BTC-USD")
            gap,momentum = long_horizon_regime(market,i,btc)
            return 0 if gap < 0 and momentum < 0 else ranks.index(btc)
        raise ValueError("UNKNOWN_CONTROL")
    return choose


def core_learner(core_dir,seed):
    """No fallback: import ONLY an exact pinned, separate BEAN Git checkout."""
    root=Path(core_dir).expanduser().resolve()
    if not (root/".git").exists():
        raise ValueError("SEPARATE_BEAN_GIT_CHECKOUT_REQUIRED")
    actual=subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
    if actual!=CORE_REVISION:
        raise ValueError("UNPINNED_BEAN_CORE")
    file=root/"bean/optimization/sponge_dqn.py"
    blob=subprocess.check_output(["git","-C",str(root),"hash-object",str(file)],text=True).strip()
    if blob!=CORE_BLOB:
        raise ValueError("MODIFIED_BEAN_LEARNER")
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    import torch
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    from bean.optimization import sponge_dqn
    if not Path(sponge_dqn.__file__).resolve().is_relative_to(root):
        raise ValueError("BEAN_IMPORT_FROM_WRONG_CHECKOUT")
    return sponge_dqn.SpongeDQN(seed=seed,observation_size=15,actions=ACTIONS,
                                domain="aiscend.shadow.rotation.v1",batch_size=16,
                                replay_capacity=2048,warmup=32,learn_every=1,
                                target_sync=100,gamma=.95,lr=.0005)


def synthetic_fixture(seed=83,count=1400):
    """Synthetic scenarios, not sampled or representative real market history."""
    rng=random.Random(seed)
    prices=[100.,100.]
    out=[]
    for t in range(count):
        for a in (0,1):
            # Switches, crashes and recoveries make a deliberately contrived curriculum.
            phase=(t//175)%4
            drift=((.007,-.002),(-.003,.008),(-.008,-.012),(.004,.007))[phase][a]
            change=drift+rng.gauss(0,.016)
            opening=prices[a]
            closing=opening*math.exp(change)
            high=max(opening,closing)*1.003
            low=min(opening,closing)*.997
            out.append({"symbol":("ALPHA-USD","BETA-USD")[a],"open_time":BAR*t,
                        "open":opening,"high":high,"low":low,"close":closing,"volume":50000.})
            prices[a]=closing
    return out


def block_interval(period_logs, *, seed, block=4, repeats=500):
    """Synthetic/development diagnostic ONLY: dependent, tiny window bootstrap."""
    if not period_logs:
        raise ValueError("NO_EVAL_PERIODS")
    blocks=[period_logs[i:i+block] for i in range(0,len(period_logs),block)]
    rng=random.Random(seed)
    estimates=sorted(math.expm1(math.fsum(x for k in range(len(blocks))
                        for x in blocks[rng.randrange(len(blocks))]))
                     for _ in range(repeats))
    return {"low":estimates[int(.05*repeats)],"high":estimates[int(.95*repeats)],
            "blocks":len(blocks),"diagnostic_only":True}


def train(core_dir,market,output,*,seeds=(7,41),epochs=4,source_kind="synthetic",
          training_policy="self"):
    """Experimental learning, no promotion. Only past matured decisions train.

    All arms share the same observation, decision cadence, price path and costs.
    Evaluation begins after the last training outcome. No holdout feedback.
    """
    if source_kind not in ("synthetic","public_historical"):
        raise ValueError("INVALID_SOURCE_KIND")
    if training_policy not in ("self","mentor","shuffled_mentor"):
        raise ValueError("INVALID_TRAINING_POLICY")
    if training_policy!="self" and source_kind!="public_historical":
        raise ValueError("MENTOR_REQUIRES_EXACT_PUBLIC_HISTORICAL_SOURCE")
    if source_kind=="public_historical" and market["symbols"]!=["BTC-USD","ETH-USD"]:
        raise ValueError("WRONG_PUBLIC_MARKET_PAIR")
    output=Path(output)
    if output.exists():
        raise FileExistsError("EXPERIMENT_OUTPUT_MUST_BE_NEW")
    output.mkdir(parents=True)
    cutoff=(int(len(market["times"])*.70)//HORIZON)*HORIZON
    begin=((LOOKBACK+HORIZON-1)//HORIZON)*HORIZON
    if cutoff-begin < 20*HORIZON or len(market["times"])-cutoff < 10*HORIZON:
        raise ValueError("INSUFFICIENT_WINDOWS")
    protocol={"schema":SCHEMA,"assets":market["symbols"],
              "source_sha256":market["source_sha256"],"core_revision":CORE_REVISION,
              "core_file_git_blob":CORE_BLOB,"actions":list(ACTIONS),
              "first_eval_index":cutoff,"training_last_outcome_index":cutoff,
              "execution_delay_bars":DELAY,"horizon_bars":HORIZON,
              "cost_per_side":COST,"fee_bps_per_side":90,"spread_bps_round_trip":10,
              "participation_limit":PARTICIPATION,
              "seeds":list(seeds),"epochs":epochs,"source_kind":source_kind,
              "training_policy":training_policy,
              "not_fresh_real_market_oos":True,
              "live_orders":False,"promotion_authorized":False}
    protocol["protocol_sha256"]=sha(protocol)
    (output/"protocol.json").write_text(canonical(protocol)+"\n")
    controls={n:rollout(market,cutoff,len(market["times"]),fixed(market,n))
              for n in ("cash","buy_hold_first","leader","trend_exit")+
              (("btc_200d_12m_defensive",) if "BTC-USD" in market["symbols"] else ())}
    # Freeze the mentor's *target assets* per training-only decision date.
    # The shuffled control deliberately breaks date correspondence in development.
    # It is never evaluated as an action provider and cannot affect inference.
    development_indices=list(range(begin,cutoff-HORIZON,HORIZON))
    targets_for_teacher={}
    if training_policy!="self":
        for i in development_indices:
            action=fixed(market,"btc_200d_12m_defensive")(None,None,i)
            targets_for_teacher[i]=targets(market,i)[action]
        if training_policy=="shuffled_mentor":
            offset=max(1,len(development_indices)//3)
            rotated=[targets_for_teacher[development_indices[(k+offset)%len(development_indices)]]
                     for k in range(len(development_indices))]
            targets_for_teacher=dict(zip(development_indices,rotated))
    results=[]
    for seed in seeds:
        model=core_learner(core_dir,seed)
        initial=model.weights_digest()
        unique=len(development_indices)
        training_events=[]
        for epoch in range(epochs):
            # Repetition trains the same finite history, not independent examples.
            def chooser(state,pos,i):
                if training_policy=="self":
                    return model.choose(state,epsilon=.16)
                # Translate the frozen dollar/asset target into the *current*
                # 3-action leader/runner-up vocabulary without aliasing.
                return targets(market,i).index(targets_for_teacher[i])
            trial=rollout(market,begin,cutoff,
                    chooser,learn=lambda s,a,r,n,d:model.observe(s,a,r,n,d))
            if trial["unfilled"]:
                raise ValueError("UNEQUAL_BUDGET_UNFILLED_TRAINING")
            for event in trial["events"]:
                training_events.append({"seed":seed,"epoch":epoch,**event})
        with (output/f"seed-{seed}.training_events.jsonl.gz").open("wb") as raw:
            with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as zipped:
                for event in training_events:zipped.write((canonical(event)+"\n").encode())
        status={"seed":seed,"training_policy":training_policy,
                "unique_training_decision_windows":unique,
                "training_passes":epochs,"training_frames":model.frames,
                "gradient_updates":model.updates,
                "weights_sha256_before":initial,
                "weights_sha256_after":model.weights_digest()}
        if not model.updates or initial==status["weights_sha256_after"]:
            raise AssertionError("NO_REAL_CORE_WEIGHT_UPDATES")
        result=rollout(market,cutoff,len(market["times"]),
                       lambda s,p,i:model.choose(s,epsilon=0.))
        status["evaluation"]=result
        untouched=core_learner(core_dir,seed)
        if untouched.weights_digest()!=initial:
            raise AssertionError("UNTRAINED_CONTROL_CHANGED")
        status["untrained_evaluation"]=rollout(market,cutoff,len(market["times"]),
                                                lambda s,p,i:untouched.choose(s,epsilon=0.))
        state=model.training_state()
        restored=type(model).from_training_state(state)
        if restored.weights_digest()!=model.weights_digest():
            raise AssertionError("BEAN_CHECKPOINT_WEIGHT_MISMATCH")
        repeated=rollout(market,cutoff,len(market["times"]),
                         lambda s,p,i:restored.choose(s,epsilon=0.))
        if repeated["event_sha256"] != result["event_sha256"]:
            raise AssertionError("BEAN_CHECKPOINT_REPLAY_MISMATCH")
        status["checkpoint_replay_verified"]=True
        status["diagnostic_bootstrap_90pct"] = block_interval(
            result["period_log_returns"],seed=seed)
        status["excess_vs_cash"] = result["net_return"] - controls["cash"]["net_return"]
        status["excess_vs_buy_hold"] = result["net_return"] - controls["buy_hold_first"]["net_return"]
        with (output/f"seed-{seed}.training_state.json.gz").open("wb") as raw:
            with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as zipped:
                zipped.write(canonical(state).encode())
        status["checkpoint_sha256"]=hashlib.sha256((output/f"seed-{seed}.training_state.json.gz").read_bytes()).hexdigest()
        results.append(status)
    if len({x["training_frames"] for x in results})>1:
        raise ValueError("UNEQUAL_LEARNER_SAMPLE_BUDGETS")
    report={"protocol":protocol,"controls":controls,"learned":results,
            "result_status":("EXPERIMENTAL_SYNTHETIC_NO_PROFITABILITY_CLAIM"
                             if source_kind=="synthetic" else
                             "RETROSPECTIVE_PUBLIC_MARKET_NOT_PROSPECTIVE_PROFITABILITY"),
            "train_heldout_leakage":False,"promotion_authorized":False,"live_orders":False}
    (output/"results.json").write_text(canonical(report)+"\n")
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bean-core",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--source-jsonl",type=Path)
    p.add_argument("--source-manifest",type=Path)
    p.add_argument("--training-policy",choices=("self","mentor","shuffled_mentor"),default="self")
    p.add_argument("--synthetic-fixture",action="store_true")
    opts=p.parse_args(argv)
    if bool(opts.source_jsonl)==bool(opts.synthetic_fixture):
        p.error("Choose exactly one public JSONL source or synthetic fixture")
    if opts.synthetic_fixture:
        if opts.source_manifest is not None:p.error("Synthetic fixture cannot have a public source manifest")
        rows=synthetic_fixture()
        kind="synthetic"
    else:
        if opts.source_manifest is None:p.error("Public source requires its immutable manifest")
        with opts.source_jsonl.open() as f:rows=[json.loads(line) for line in f if line.strip()]
        from research.coinbase_public_paired_sixhour import START,END,PRODUCTS,digest
        manifest=json.loads(opts.source_manifest.read_text())
        if (manifest.get("schema")!="aiscend.public_coinbase.sixhour.snapshot.v1" or
                manifest.get("products")!=list(PRODUCTS) or
                manifest.get("start")!=START or manifest.get("end_exclusive")!=END or
                manifest.get("rows_sha256")!=digest(rows) or
                manifest.get("manifest_sha256")!=digest({k:v for k,v in manifest.items() if k!="manifest_sha256"})):
            raise ValueError("MISMATCHED_PUBLIC_SOURCE_MANIFEST")
        kind="public_historical"
    report=train(opts.bean_core,load_bars(rows),opts.output,source_kind=kind,
                 training_policy=opts.training_policy)
    print(canonical({"protocol_sha256":report["protocol"]["protocol_sha256"],
                     "learned_returns":[x["evaluation"]["net_return"] for x in report["learned"]],
                     "cash_return":report["controls"]["cash"]["net_return"],
                     "promotion_authorized":False,"live_orders":False}))

if __name__=="__main__":main()
