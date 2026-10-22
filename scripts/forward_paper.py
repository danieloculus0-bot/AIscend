"""Run an independent, timestamped Coinbase paper observation from Python.

Manifest JSON is a list such as:
[{"product":"BTC-USD","policy":"trend","training_only_frozen":true}]
Write this manifest BEFORE observing forward candles and do not change it.
Only publicly completed Coinbase candles are read. No keys or trades used.
"""
import argparse
import json
from pathlib import Path

from src.forward_paper import observe_and_score


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--db",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--fee-bps",type=float,default=60)
    parser.add_argument("--spread-bps",type=float,default=10)
    args=parser.parse_args(argv)
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
