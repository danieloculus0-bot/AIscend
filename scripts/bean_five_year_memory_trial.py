"""Paired BEAN-Core blind five-year replay with LIVE-DURING-REPLAY compact memory.

Original frozen Coinbase CSV is read only by evaluator. Neither BEAN policy
receives future candles. Memory starts EMPTY and adds SHA-linked indexed rows
only after each simulated exit; it MUST NOT preload the full 2021-2026 ledger,
because that would leak future information. The two policies both use ACTUAL
BEAN Core SpongeTD and LearningRuntime, with one also receiving prior-only
memory lookup evidence. No live orders or trading credentials.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import date, timedelta
import csv
import json
from pathlib import Path
import sqlite3
import tempfile

from scripts.bean_five_year_blind import (
    BeanBridge, _WORKER, DATA, ENTRY_DELAY, END_EXCLUSIVE,
    FEE_BPS, HOLD, LOOKBACK, PRODUCTS, SPREAD_BPS, START, STEP,
    canon, digest, fee_net, load, signal_state,
)
from src.bean_pair_index import encode_prefix, encode
from src.bean_cause_ledger import classify, CODES

SCHEMA = "aiscend.bean-memory-paired-five-year.v1"
# Frozen a priori. Do not optimize these against results in this run.
MIN_EXPERIENCES = 8
SIMILARITY_FLOOR = 2
MAX_CANDIDATES = 64
MEMORY_WEIGHT = 0.35

# Separate worker: true BEAN Core LearningRuntime for BOTH arms.
# Memory retrieval is strictly from host's dated ledger, never from prices.
MEMORY_WORKER = r"""
import json,sys
from bean.learning.runtime import LearningRuntime
learners={
    "memory": LearningRuntime(sys.argv[1],actions=("trade","cash"),seed=17),
    "control": LearningRuntime(sys.argv[2],actions=("trade","cash"),seed=17),
}
try:
    for line in sys.stdin:
        x=json.loads(line)
        service=learners[x["arm"]]
        command=x["cmd"]
        if command=="choose":
            state=x["state"]
            scoped=service.scoped("aiscend-five-year-blind",state)
            q=list(service.policy.values(scoped))
            memory=x.get("memory")
            if x["arm"]=="control" and memory is not None:
                raise ValueError("Control must not receive memory")
            if memory is None or memory["count"]<8:
                ai=service.policy.choose(scoped, explore=True)
            else:
                # Real BEAN Q-values + pre-existing empirical pattern evidence;
                # no coded guarantee that memory has positive edge.
                adj=[q[0]+0.35*memory["shrunk_trade_edge_bps"],q[1]]
                if service.policy.rng.random()<service.policy.epsilon:
                    ai=service.policy.rng.randrange(2)
                else:
                    ai=0 if adj[0]>=adj[1] else 1
            action=service.policy.actions[ai]
            actual=service.choose(
                episode_id=x["episode"], domain="aiscend-five-year-blind",
                state=state,location_ref=state,legal_actions=[action])
            result={"action":actual["action"],"q_before":q,
                    "updates_before":service.policy.steps}
        elif command=="feedback":
            result=service.feedback(
                episode_id=x["episode"],reward=x["reward"],done=True)
            scoped=service.scoped("aiscend-five-year-blind",x["state"])
            result["q_after"]=list(service.policy.values(scoped))
        elif command=="checkpoint":
            result=service.checkpoint(
                baseline=0.,candidate=0.,samples=x["samples"],
                evaluation_sha256=x["digest"])
        elif command=="status":
            result=service.status()
        else:
            raise ValueError("Unknown worker operation")
        sys.stdout.write(json.dumps(result,sort_keys=True)+"\n")
        sys.stdout.flush()
finally:
    for service in learners.values():
        service.close()
"""


class PairedBean(BeanBridge):
    """Use the existing credential-free bridge, with paired BEAN brains."""
    def __init__(self, core_dir, scratch):
        from src.bean_core_bridge import core_identity
        import os
        import subprocess
        import sys
        self.identity=core_identity(core_dir)
        self.proc=subprocess.Popen(
            [sys.executable,"-u","-c",MEMORY_WORKER,
             str(scratch/"memory-bean.sqlite3"),
             str(scratch/"control-bean.sqlite3")],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,bufsize=1,env={
                "PYTHONPATH":str(Path(core_dir).resolve()),
                "PATH":os.environ.get("PATH",""),
                "PYTHONDONTWRITEBYTECODE":"1",
            })


class RetentionLedger:
    """One compact pair index and SHA-receipted experience per scored episode."""
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        self.db.execute("""CREATE TABLE memories (
            seq INTEGER PRIMARY KEY, episode TEXT NOT NULL UNIQUE,
            product TEXT NOT NULL, decision_day TEXT NOT NULL,
            exit_day TEXT NOT NULL, code TEXT NOT NULL,
            prefix TEXT NOT NULL, diagnostic TEXT NOT NULL,
            action TEXT NOT NULL, net_bps REAL NOT NULL,
            evidence_sha TEXT NOT NULL,
            prior_sha TEXT NOT NULL, receipt_sha TEXT NOT NULL UNIQUE)""")
        self.db.execute("CREATE INDEX memory_by_date ON memories(product,exit_day)")
        self.tip="GENESIS"

    def add(self, *, episode, product, state, action, decision_day, exit_day,
            net_return, gross_return, evidence_sha):
        code=classify(action,gross_bps=10000*gross_return,
                      net_bps=10000*net_return)
        prefix=encode_prefix(product,state)
        idx=encode(product=product,state=state,action=action,
                   root_cause_code=code,net_return=net_return)
        if not (len(idx)==16 and prefix==idx[:10]):
            raise AssertionError("16-byte memory index and condition prefix mismatch")
        n=self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]+1
        receipt={
            "seq":n,"episode":episode,"product":product,
            "decision_day":decision_day,"exit_day":exit_day,
            "code":idx,"prefix":prefix,"diagnostic":code,
            "action":action,"net_bps":round(10000*net_return,8),
            "evidence_sha":evidence_sha,"prior_sha":self.tip,
        }
        sha=digest(canon(receipt))
        with self.db:
            self.db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(receipt[k] for k in (
                    "seq","episode","product","decision_day","exit_day",
                    "code","prefix","diagnostic","action","net_bps",
                    "evidence_sha","prior_sha"))+(sha,))
        self.tip=sha
        return receipt|{"receipt_sha":sha}

    def prior(self, *, product, state, as_of):
        """Only exited before as_of. Never read current or later outcomes."""
        index=encode_prefix(product,state)
        all_rows=self.db.execute(
            "SELECT * FROM memories WHERE product=? AND exit_day<?"
            " ORDER BY exit_day DESC,seq DESC",
            (product,as_of)).fetchall()
        choices=[]
        for r in all_rows:
            same=sum(index[p:p+2]==r["prefix"][p:p+2]
                     for p in (2,4,6,8))
            if same >= SIMILARITY_FLOOR:
                choices.append((same,dict(r)))
        choices.sort(key=lambda t:(-t[0],-t[1]["seq"]))
        chosen=choices[:MAX_CANDIDATES]
        count=len(chosen)
        if count:
            total=sum(r["net_bps"]*(m/4) for m,r in chosen)
            weight=sum(m/4 for m,_ in chosen)
            empirical=total/weight
        else:
            empirical=0.0
        shrink=count/(count+20)
        return {
            "count":count,
            "shrunk_trade_edge_bps":round(empirical*shrink,6)
              if count>=MIN_EXPERIENCES else 0.0,
            "reference_codes":dict(sorted(Counter(
                r["diagnostic"] for _,r in chosen).items())),
            "query_index_prefix":index,
            "query_snapshot_tip":self.tip,
            "latest_matched_exit_day":max(
                (r["exit_day"] for _,r in chosen),default=None),
            "as_of":as_of,
            "prior_only":True,
        }

    def validate(self):
        tip="GENESIS"
        rows=self.db.execute("SELECT * FROM memories ORDER BY seq").fetchall()
        for i,r in enumerate(rows,1):
            payload=dict(r)
            actual=payload.pop("receipt_sha")
            if i!=payload["seq"] or payload["prior_sha"]!=tip or (
                    digest(canon(payload))!=actual):
                raise ValueError("Corrupt compact memory receipt at "+str(i))
            if payload["decision_day"]>=payload["exit_day"]:
                raise ValueError("Memory outcome precedes decision")
            tip=actual
        return {"rows":len(rows),"receipt_tip_sha256":tip}

    def export(self, path):
        rows=self.db.execute("SELECT * FROM memories ORDER BY seq").fetchall()
        with Path(path).open("w",newline="",encoding="utf-8") as stream:
            writer=csv.writer(stream)
            writer.writerow(rows[0].keys())
            writer.writerows(tuple(x) for x in rows)

    def close(self):
        self.db.close()


def run(*, data_path, core_dir, output_dir):
    data,meta=load(data_path)
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    dates=[START+timedelta(days=i) for i in range((END_EXCLUSIVE-START).days)]
    steps=list(range(LOOKBACK,len(dates)-HOLD,STEP))
    equity={a:{p:500.0 for p in PRODUCTS}
            for a in ("memory","control","always_cash","always_trade")}
    total={a:{"trade":0,"cash":0} for a in ("memory","control")}
    evidence=[]
    with tempfile.TemporaryDirectory(prefix="bean-memory-blind-") as temp:
        bridge=PairedBean(core_dir,Path(temp))
        ledger=RetentionLedger(Path(temp)/"experience.sqlite3")
        try:
            for step in steps:
                day=dates[step]
                entry=dates[step+ENTRY_DELAY]
                exit_day=dates[step+HOLD]
                pending=[]
                # Both coins choose before either future price is released.
                for product in PRODUCTS:
                    state=signal_state(data,product,day)
                    episode=product+":"+day.isoformat()
                    memory=ledger.prior(
                        product=product,state=state,as_of=day.isoformat())
                    control=bridge.call({
                        "arm":"control","cmd":"choose","episode":episode,"state":state})
                    candidate=bridge.call({
                        "arm":"memory","cmd":"choose","episode":episode,
                        "state":state,"memory":memory})
                    for arm,choice in (("control",control),("memory",candidate)):
                        total[arm][choice["action"]]+=1
                    receipt={
                        "episode":episode,"product":product,
                        "signal_day":day.isoformat(),"entry_day":entry.isoformat(),
                        "exit_day":exit_day.isoformat(),"state":state,
                        "memory_snapshot":memory,
                        "control_action_pre_entry":control["action"],
                        "memory_action_pre_entry":candidate["action"],
                        "control_q_before":control["q_before"],
                        "memory_q_before":candidate["q_before"],
                        "prior_decision_digest":evidence[-1]["outcome_sha"]
                        if evidence else "GENESIS",
                    }
                    receipt["decision_sha"]=digest(canon(receipt))
                    pending.append((product,state,receipt))
                for product,state,receipt in pending:
                    open_price=data[(product,entry)]["open"]
                    exit_price=data[(product,exit_day)]["open"]
                    actual=fee_net(open_price,exit_price)
                    gross=exit_price/open_price-1
                    for arm,action in (
                        ("memory",receipt["memory_action_pre_entry"]),
                        ("control",receipt["control_action_pre_entry"]),
                        ("always_trade","trade"),("always_cash","cash")):
                        if action=="trade":
                            equity[arm][product]*=1+actual
                    for arm in ("control","memory"):
                        action=receipt[arm+"_action_pre_entry"]
                        feedback=bridge.call({
                            "arm":arm,"cmd":"feedback","episode":receipt["episode"],
                            "state":state,"reward":10000*actual if action=="trade" else 0.0})
                        receipt[arm+"_q_after"]=feedback["q_after"]
                        receipt[arm+"_updates"]=feedback["q_updates"]
                    receipt.update({
                        "entry_open":round(open_price,8),
                        "exit_open":round(exit_price,8),
                        "gross_trade_return":round(gross,10),
                        "net_fee_adjusted_trade_return":round(actual,10),
                        "memory_sleeve_usd":round(equity["memory"][product],8),
                        "control_sleeve_usd":round(equity["control"][product],8),
                    })
                    receipt["outcome_sha"]=digest(canon(receipt))
                    receipt["experience"]=ledger.add(
                        episode=receipt["episode"],product=product,
                        state=state,action=receipt["memory_action_pre_entry"],
                        decision_day=day.isoformat(),
                        exit_day=exit_day.isoformat(),
                        net_return=actual,gross_return=gross,
                        evidence_sha=receipt["outcome_sha"])
                    evidence.append(receipt)
            ledger_receipt=ledger.validate()
            if ledger_receipt["rows"]!=len(evidence):
                raise AssertionError("Lost compact memories")
            ledger.export(output_dir/"experiences.csv")
            import shutil
            ledger.db.commit()
            ledger.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copyfile(ledger.path,output_dir/"experiences.sqlite3")
            learner_status={arm:bridge.call({"arm":arm,"cmd":"status"})
                            for arm in ("control","memory")}
        finally:
            ledger.close()
            bridge.close()

    final={k:round(sum(v.values()),4) for k,v in equity.items()}
    summary={
        "schema":SCHEMA,"dataset_sha256":meta["csv_sha256"],
        "real_bean_core_sha":bridge.identity["sha"],
        "opportunities":len(evidence),"initial_usd":1000,
        "final_equity_usd":final,
        "net_return_pct":{k:round((v/1000-1)*100,4) for k,v in final.items()},
        "actions":total,
        "retention":ledger_receipt,
        "lookups_with_8plus_prior_examples":sum(
            x["memory_snapshot"]["count"]>=8 for x in evidence),
        "leakage_assertion":"all memory queries exit_day < decision day",
        "memory_method":"SpongeTD Q margin + 0.35 * shrinkage weighted fee-net prior",
        "selection_parameters_frozen_before_test":True,
        "fee_bps_each_side":FEE_BPS,"spread_bps":SPREAD_BPS,
        "true_trading_or_order_execution":False,
        "historical_simulation_not_live_profitability_proof":True,
        "core_q_updates":{k:v["q_updates"] for k,v in learner_status.items()},
    }
    if summary["opportunities"]!=496 or any(x!=496 for x in summary["core_q_updates"].values()):
        raise AssertionError("Incomplete blind paired memory comparison")
    with (output_dir/"decisions.jsonl").open("w",encoding="utf-8") as out:
        for row in evidence:
            out.write(json.dumps(row,sort_keys=True)+"\n")
    (output_dir/"summary.json").write_text(json.dumps(
        summary,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,sort_keys=True))
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,default=DATA)
    parser.add_argument("--core",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,default=Path(
        "artifacts/bean-five-year-memory-trial"))
    args=parser.parse_args()
    run(data_path=args.data,core_dir=args.core,output_dir=args.output_dir)


if __name__=="__main__":
    main()
