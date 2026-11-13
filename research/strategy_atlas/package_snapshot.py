"""Rebuild the database, import captured headlines and export a BEAN packet."""
from __future__ import annotations
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.strategy_atlas import Atlas,build_database,compress_database,read_jsonl,utc_seconds
from src.narrative_evidence import ingest_documents,narrative_context

def package(root=None,database=None):
    root=Path(root or Path(__file__).parent)
    database=Path(database or root.parents[1]/"artifacts"/"strategy_research.sqlite3")
    build_database(root,database)
    records=read_jsonl(root/"narrative_documents.jsonl")
    added=ingest_documents(database,records)
    # A frozen packet is reproducible from the collected evidence, not today's clock.
    asof=max(utc_seconds(r["available_at"]) for r in records)+1 if records else "2026-10-24T23:59:59Z"
    with Atlas(database) as a:
        context=narrative_context(a,as_of=asof)
        stats=a.stats()
    (root/"BEAN_CONTEXT.json").write_text(json.dumps(context,indent=2,sort_keys=True,ensure_ascii=False)+"\n")
    receipt=compress_database(database,root/"strategy_research.sqlite3.gz")
    receipt["path"]="strategy_research.sqlite3.gz"
    receipt["stats"]=stats
    receipt["headlines_inserted"]=added
    receipt["context_sha256"]=context["content_sha256"]
    receipt["scope"]="research_collection_and_software_contracts; no profitability or live execution demonstrated"
    (root/"snapshot_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
    print(json.dumps(receipt,indent=2))
    return receipt

if __name__=="__main__":package()
