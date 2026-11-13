"""Build, query and export AIscend's source-linked research atlas."""
from __future__ import annotations
import argparse
import json
import sqlite3
import time
from pathlib import Path

from src.strategy_atlas import Atlas,build_database,compress_database,evaluate_recipe,read_jsonl,append_experiment,recall_experiments
from src.narrative_evidence import collect_feeds,ingest_documents,narrative_context

ROOT=Path(__file__).resolve().parents[1]/"research"/"strategy_atlas"

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,default=ROOT/"strategy_research.sqlite3.gz")
    sub=p.add_subparsers(dest="command",required=True)
    b=sub.add_parser("build");b.add_argument("--root",type=Path,default=ROOT);b.add_argument("--output",type=Path,default=Path("artifacts/strategy_research.sqlite3"));b.add_argument("--compress",type=Path)
    sub.add_parser("stats")
    q=sub.add_parser("query");q.add_argument("--family");q.add_argument("--text");q.add_argument("--limit",type=int,default=20)
    h=sub.add_parser("history");h.add_argument("asset");h.add_argument("--metric");h.add_argument("--start");h.add_argument("--end");h.add_argument("--limit",type=int,default=10)
    c=sub.add_parser("context");c.add_argument("--as-of");c.add_argument("--hours",type=float,default=24);c.add_argument("--output",type=Path)
    r=sub.add_parser("rule");r.add_argument("strategy_id");r.add_argument("--snapshot",type=Path,required=True)
    n=sub.add_parser("collect-news");n.add_argument("--feeds",nargs="+",required=True);n.add_argument("--output",type=Path,required=True)
    i=sub.add_parser("ingest-news");i.add_argument("--input",type=Path,required=True)
    e=sub.add_parser("record-result");e.add_argument("--input",type=Path,required=True)
    recall=sub.add_parser("recall");recall.add_argument("--as-of",required=True);recall.add_argument("--strategy-id")
    args=p.parse_args(argv)
    if args.command=="build":
        build_database(args.root,args.output)
        result=compress_database(args.output,args.compress) if args.compress else {"database":str(args.output)}
    elif args.command=="collect-news":
        records,status=collect_feeds(args.feeds)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text("".join(json.dumps(r,sort_keys=True)+"\n" for r in records))
        result={"records":len(records),"sources":status}
        args.output.with_suffix(".manifest.json").write_text(json.dumps(result,indent=2)+"\n")
    elif args.command=="ingest-news":
        if args.db.suffix==".gz":p.error("Ingest into an uncompressed working DB; the published snapshot is immutable")
        result={"inserted":ingest_documents(args.db,read_jsonl(args.input))}
    elif args.command=="record-result":
        if args.db.suffix==".gz":p.error("Record experiments in an uncompressed working DB")
        result={"ledger_tip":append_experiment(args.db,json.loads(args.input.read_text()))}
    elif args.command=="recall":
        if args.db.suffix==".gz":
            with Atlas(args.db) as atlas:result=recall_experiments(atlas.path if atlas._temporary is None else Path(atlas._temporary.name)/"atlas.sqlite3",as_of=args.as_of,strategy_id=args.strategy_id)
        else:result=recall_experiments(args.db,as_of=args.as_of,strategy_id=args.strategy_id)
    else:
        with Atlas(args.db) as atlas:
            if args.command=="stats":result=atlas.stats()
            elif args.command=="query":result=atlas.query(family=args.family,text=args.text)[:args.limit]
            elif args.command=="history":
                import itertools
                result=list(itertools.islice(atlas.history(args.asset,metric=args.metric,start=args.start,end=args.end),max(0,args.limit)))
            elif args.command=="context":result=narrative_context(atlas,as_of=args.as_of or time.time(),lookback_hours=args.hours)
            elif args.command=="rule":
                snap=json.loads(args.snapshot.read_text())
                result=evaluate_recipe(atlas.recipe(args.strategy_id),snap["features"],decision_at=snap["decision_at"],feature_available_at=snap["feature_available_at"],position=snap.get("position",0))
        if args.command=="context" and args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    return 0

if __name__=="__main__":raise SystemExit(main())
