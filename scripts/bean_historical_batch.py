"""One Coinbase historical download, multiple local strategy exams.

Reuses immutable public candle observations to avoid duplicate API calls. The
30d and 60d windows overlap and must NOT be presented as independent proof.
"""
from __future__ import annotations
import json
from pathlib import Path
import time

from scripts.bean_historical_replay import collect, assess
from scripts.bean_strategy_gate import judge
from scripts.bean_strategy_school import learn


def examine_dataset(data: dict) -> dict:
    end = int(data["end"])
    cutoff = end - 30 * 86400
    recent = {**data, "start": cutoff,
              "bars": [bar for bar in data["bars"] if int(bar["ts"]) >= cutoff]}
    if len(recent["bars"]) < 120:
        raise ValueError("Insufficient genuine recent candles")
    report30 = assess(recent)
    report60 = assess(data)
    return {"30d": report30, "60d": report60,
            "promotion": judge([report30, report60]),
            "school": learn(recent),
            "overlapping_windows": True}


def main():
    import argparse
    from scripts.bean_historical_replay import PAIRS
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    end = int(time.time() // 3600 * 3600)
    dataset = collect(end - 60 * 86400, end, pairs=PAIRS)
    reports = examine_dataset(dataset)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "30d": "bean-historical-30d.json",
        "60d": "bean-historical-60d.json",
        "promotion": "bean-promotion-gate.json",
        "school": "bean-school-report.json",
    }
    for key, name in files.items():
        (args.out_dir / name).write_text(
            json.dumps(reports[key], indent=2) + "\n", encoding="utf-8")
    # Reproducible evidence for replays and external audits.
    (args.out_dir / "bean-school-dataset.json").write_text(
        json.dumps(dataset, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "pairs": dataset["pairs"],
        "candles_downloaded_once": len(dataset["bars"]),
        "holdout_net_30d": reports["30d"]["holdout"]["net_return"],
        "holdout_net_60d": reports["60d"]["holdout"]["net_return"],
        "school_holdout_net": reports["school"]["holdout"]["net_return"],
        "promoted": reports["promotion"]["accepted"],
        "warning": "30d and 60d windows overlap; independent validation required",
    }, indent=2))


if __name__ == "__main__":
    main()
