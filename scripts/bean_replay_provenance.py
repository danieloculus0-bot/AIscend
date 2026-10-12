"""Add auditable chronology and cost assumptions to a historical replay.

This attaches metadata from the exact in-memory dataset used by the examiner.
It does not authenticate an external source or certify untouched future data.
"""
import hashlib
import json

from scripts.bean_historical_replay import COST

FILL_POLICY = "previous_close_signal_next_open_fill"


def attest_report(report: dict, dataset: dict) -> dict:
    bars = dataset["bars"]
    cut = int(len(bars) * 0.60)
    if not 0 < cut < len(bars):
        raise ValueError("Insufficient chronological observations")
    if report["training_bars"] != cut or report["holdout_bars"] != len(bars) - cut:
        raise ValueError("Report counts disagree with dataset")
    if report["historical_source"] != dataset["source"]:
        raise ValueError("Source label disagrees with dataset")
    if list(report["pairs"]) != list(dataset["pairs"]):
        raise ValueError("Market coverage disagrees with dataset")
    if any(b["ts"] - a["ts"] != 3600 for a, b in zip(bars, bars[1:])):
        raise ValueError("Non-contiguous hourly evidence")
    canonical = json.dumps({"source": dataset["source"],
                            "pairs": dataset["pairs"], "bars": bars},
                           sort_keys=True, separators=(",", ":"))
    result = dict(report)
    result.update({
        "dataset_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "training_window": {"start_ts": int(bars[0]["ts"]),
                            "end_ts": int(bars[cut]["ts"])},
        "holdout_window": {"start_ts": int(bars[cut]["ts"]),
                           "end_ts": int(bars[-1]["ts"]) + 3600},
        "cost_per_side": COST,
        "fill_policy": FILL_POLICY,
        "provenance_limit": "Host-supplied source label; SHA-256 proves input identity, not exchange authenticity.",
    })
    return result
