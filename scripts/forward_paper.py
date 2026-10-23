"""Run an independent, timestamped Coinbase paper observation from Python.

Manifest JSON is a list such as:
[{"product":"BTC-USD","policy":"trend","training_only_frozen":true}]
Write this manifest BEFORE observing forward candles and do not change it.
Only publicly completed Coinbase candles are read. No keys or trades used.
"""
import argparse
import json
from pathlib import Path

from src.forward_paper import observe_and_score, freeze_training_candidates


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest",type=Path)
    source.add_argument("--freeze-aggregate",type=Path,
                        help="Save training-ranked candidates as a new manifest")
    parser.add_argument("--db",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--fee-bps",type=float,default=60)
    parser.add_argument("--spread-bps",type=float,default=10)
    args=parser.parse_args(argv)
    if args.freeze_aggregate:
        if not args.output:
            parser.error("--freeze-aggregate requires --output manifest.json")
        report=json.loads(args.freeze_aggregate.read_text(encoding="utf-8"))
        frozen=freeze_training_candidates(report)
        if args.output.exists():
            raise FileExistsError("Cannot overwrite an already frozen paper manifest")
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(frozen,sort_keys=True,indent=2)+"\n",
                               encoding="utf-8")
        print("Frozen",len(frozen),"training-ranked Coinbase policies:",args.output)
        return 0
    manifest=json.loads(args.manifest.read_text(encoding="utf-8"))
    result=observe_and_score(
        manifest=manifest, db_path=args.db,
        fee_bps=args.fee_bps, spread_bps=args.spread_bps)
    encoded=json.dumps(result,sort_keys=True,indent=2)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(encoded+"\n",encoding="utf-8")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
