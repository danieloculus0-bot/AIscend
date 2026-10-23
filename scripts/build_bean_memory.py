"""Materialize immutable 5-year decisions as inspectable BEAN memory layers.

Run after the blind evaluator has finished: no new decision or Q update.
Never replace original decisions, original Coinbase CSV, or history receipts.
Outputs SQLite, 16-byte index CSV, root-cause CSV, codebooks, hierarchy
and a SHA-attributed build receipt, all reproducibly committed by CI.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path

from src.bean_cause_ledger import CODES, write_index, verify_index
from src.bean_memory_subsets import SubsetLedger
from src.bean_pair_index import REFERENCES, SLOTS, encode, reference_document


def after_close(day):
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()+"T00:00:01Z"


def register_pairs(ledger, set_id, descriptions):
    """Define each type once and each subset once, scoped by table+slot."""
    ledger.define_set(set_id, set_id.replace("_", " "))
    for slot,(field,pairs) in enumerate(descriptions,1):
        for code,meaning in sorted(pairs.items()):
            ledger.define_type(
                set_id=set_id,slot=slot,character=code[0],meaning=field)
            ledger.define_subset(
                set_id=set_id,slot=slot,code=code,meaning=meaning)


def build(*, source, summary, output_dir):
    out=Path(output_dir)
    out.mkdir(parents=True,exist_ok=True)
    result=write_index(
        source,database=out/"cause-ledger.sqlite3",
        csv_path=out/"root-cause-decisions.csv",
        codebook_path=out/"root-cause-codes.json",
        original_summary=summary)
    integrity=verify_index(out/"cause-ledger.sqlite3")
    if integrity["rows"]!=result["rows"] or (
            integrity["receipt_tip_sha256"]!=result["latest_receipt_sha"]):
        raise AssertionError("Compacted decisions lost integrity")

    with (out/"root-cause-decisions.csv").open(newline="",encoding="utf-8") as stream:
        rows=list(csv.DictReader(stream))
    if len(rows)!=496:
        raise AssertionError("Unexpected number of frozen five-year decisions")

    root=SubsetLedger(out/"inspection-hierarchy.sqlite3")
    try:
        register_pairs(root,"inspection_signal",[
            (field,REFERENCES[field]) for field,_ in SLOTS[:5]])
        register_pairs(root,"inspection_action",[
            ("BEAN choice",REFERENCES["action"])])
        register_pairs(root,"inspection_outcome",[
            ("fee-net outcome band",REFERENCES["net_return_band"])])
        register_pairs(root,"inspection_root_cause",[
            ("post-outcome diagnostic",{
                code:CODES[code][0]+" : "+CODES[code][2]
                for code in CODES})])

        refs=[]
        for item in rows:
            state={key:item[key] for key in (
                "momentum_7d","momentum_28d",
                "momentum_84d","volatility_14d")}
            code=encode(
                product=item["product"],state=state,
                action=item["action"],root_cause_code=item["code"],
                net_return=float(item["net_bps"])/10000.0)
            if len(code.encode("ascii"))!=16:
                raise AssertionError("A retained eight-pair index is not 16 bytes")
            origin=item["source_decision_sha256"]
            after=item["source_outcome_sha256"]
            episode=item["episode"]
            decision_at=after_close(item["decision_date"])
            exit_at=after_close(item["exit_date"])
            signal=root.add(
                set_id="inspection_signal",code=code[:10],
                meaning="Past-only market state",
                stage="signal",available_at=decision_at,
                evidence_sha256=origin,source_episode=episode)
            action=root.add(
                set_id="inspection_action",code=code[10:12],
                meaning="Pre-entry BEAN "+item["action"]+" choice",
                stage="decision",available_at=decision_at,
                evidence_sha256=origin,source_episode=episode,
                parent_id=signal)
            outcome=root.add(
                set_id="inspection_outcome",code=code[14:16],
                meaning="Observed fee-net return bucket",
                stage="outcome",available_at=exit_at,
                evidence_sha256=after,source_episode=episode,
                parent_id=action)
            rootcause=root.add(
                set_id="inspection_root_cause",code=code[12:14],
                meaning="Post-exit diagnostic "+item["code"],
                stage="root_cause",available_at=exit_at,
                evidence_sha256=after,source_episode=episode,
                parent_id=outcome)
            refs.append({
                "seq":int(item["seq"]),
                "episode":episode,
                "index_16_ascii_bytes":code,
                "signal_node_sha256":signal,
                "decision_node_sha256":action,
                "outcome_node_sha256":outcome,
                "root_cause_node_sha256":rootcause,
                "evidence_sha256":item["source_outcome_sha256"],
            })
        verified=root.verify()
        if verified["nodes"]!=4*len(rows):
            raise AssertionError("Missing one of four inspection layers")
    finally:
        root.close()

    index_file=out/"indexed-16-byte-records.csv"
    with index_file.open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(refs[0]))
        writer.writeheader()
        writer.writerows(refs)
    (out/"type-and-subset-tables.json").write_text(
        json.dumps(reference_document(),sort_keys=True,indent=2)+"\n")
    receipt={
        "schema":"aiscend.bean.compressed-memory-export.v1",
        "decision_records":len(refs),
        "inspection_nodes":verified["nodes"],
        "layers":["signal","decision","outcome","root_cause"],
        "index_bytes_per_record":16,
        "raw_decisions_preserved":True,
        "no_future_outcome_in_pre_entry_layers":True,
        "root_cause_codes_diagnostic_not_proven_causation":True,
        "source_outcome_sha256":result["source_outcome_sha"],
        "compact_receipt_sha256":integrity["receipt_tip_sha256"],
        "hierarchy_receipt_sha256":verified["receipt_tip_sha256"],
        "root_code_counts":result["root_code_counts"],
        "net_pnl_usd":result["net_pnl_usd"],
        "github_commit_of_results":"performed_by_separate_ci_step",
    }
    (out/"memory-receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True)+"\n")
    print(json.dumps(receipt,sort_keys=True))
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=Path(
        "research/bean-five-year-blind/decisions.jsonl"))
    parser.add_argument("--summary",type=Path,default=Path(
        "research/bean-five-year-blind/summary.json"))
    parser.add_argument("--output-dir",type=Path,default=Path(
        "artifacts/bean-memory-retention"))
    args=parser.parse_args(argv)
    build(source=args.source,summary=args.summary,output_dir=args.output_dir)


if __name__=="__main__":
    main()
