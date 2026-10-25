"""Verify/reuse a retained BEAN research policy without replaying future learning.

This command loads actual Core state and reports its fingerprint. It does not
submit orders. A policy may not be reused before its recorded knowledge cutoff.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
from src.bean_causal_simulation import Worker
from src.strategy_atlas import utc_seconds

def verify(directory,core_dir,policy_id,as_of):
    directory=Path(directory)
    bank=json.loads((directory/"retention_manifest.json").read_text())
    item=next(x for x in bank["policies"] if x["id"]==policy_id)
    if utc_seconds(as_of)<utc_seconds(item["knowledge_cutoff"]):raise ValueError("Retained policy contains knowledge later than requested decision time")
    path=directory/item["file"]
    if hashlib.sha256(path.read_bytes()).hexdigest()!=item["file_sha256"]:raise ValueError("Retained policy artifact changed")
    with tempfile.TemporaryDirectory(prefix="bean-policy-recovery-") as temp:
        restored=Path(temp)/"policy.sqlite3"
        restored.write_bytes(gzip.decompress(path.read_bytes()))
        worker=Worker(core_dir,restored,item["engine"],item["seed"],restore=True)
        try:
            status=worker.call("status")
            if status["weights_sha256"]!=item["weights_sha256"] or status["updates"]!=item["updates"]:raise ValueError("Recovered learning differs from measured policy")
            action=None
            if item.get("diagnostic_observation"):
                p=item["diagnostic_observation"]
                action=worker.call("choose",vector=p["vector"],bins=p["bins"])["action"]
                if item.get("expected_diagnostic_action") is not None and action!=item["expected_diagnostic_action"]:raise ValueError("Recovered decision differs")
            return {"schema":"aiscend.bean.retained_policy_receipt.v1","policy_id":policy_id,"knowledge_cutoff":item["knowledge_cutoff"],"as_of":as_of,"recovered_state":status,"diagnostic_action_index":action,"economic_qualification":item["economic_qualification"],"live_orders":False,"scope":"State/decision recovery diagnostic, not a new market performance test"}
        finally:worker.close()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--directory",type=Path,required=True);p.add_argument("--core-dir",type=Path,required=True);p.add_argument("--policy-id",default="selected_online_matured_feedback");p.add_argument("--as-of",required=True)
    a=p.parse_args(argv);print(json.dumps(verify(a.directory,a.core_dir,a.policy_id,a.as_of),indent=2))

if __name__=="__main__":main()
