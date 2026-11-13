"""Bounded replay digestion for actual BEAN Python-source research learners.

A receipt that teaching rows were *observed* is not evidence that gradients
ever saw them. Schedule explicit updates after each bounded cohort, before
the cohort can be evicted by later observations. This does NOT establish
profitable trading or make already-inspected history unseen.
"""
from __future__ import annotations

import math


def digest_chunks(worker, ordered, *, field: str, updates: int,
                  batch_size: int, transitions_per_item: int,
                  replay_capacity: int = 4096, minimum_replay: int = 64):
    """Teach and train in alternating batches, preserving an exact update budget.

    `ordered` contains already-matured training observations only. Callers
    must lock train/evaluation cutoffs before constructing that sequence.
    `transitions_per_item` is 1 for curriculum rows and len(ACTIONS) for a
    mentor lesson. No optimizer step is taken on future evaluation data.
    """
    n = len(ordered)
    if not n or field not in ("experiences", "lessons"):
        raise ValueError("Expected nonempty, known training evidence")
    if any(type(v) is not int or v < 1 for v in (
            updates, batch_size, transitions_per_item,
            replay_capacity, minimum_replay)):
        raise ValueError("Invalid replay digestion bounds")
    if n * transitions_per_item < minimum_replay:
        raise ValueError("Not enough matured transitions to train")
    # At least one training update per batch, without leaving a batch too
    # small for the first optimizer step or large enough to evict itself.
    size = max(batch_size, math.ceil(n / updates),
               math.ceil(minimum_replay / transitions_per_item))
    if size * transitions_per_item > replay_capacity:
        raise ValueError("Replay budget too small to digest every cohort")
    batches = math.ceil(n / size)
    state = worker.call("status")
    start_frames, start_updates = int(state["frames"]), int(state["updates"])
    receipts = []
    for index in range(batches):
        first = index * size
        end = min(n, first + size)
        taught = worker.call("teach", **{field: list(ordered[first:end])})
        expected_frames = start_frames + end * transitions_per_item
        if int(taught["frames"]) != expected_frames:
            raise RuntimeError("BEAN teaching receipt lost or duplicated frames")
        target = start_updates + (updates * (index + 1)) // batches
        result = worker.call("train", updates=target)
        if int(result["updates"]) != target:
            raise RuntimeError("BEAN did not perform the scheduled gradient updates")
        receipts.append({"items_through": end, "frames_through": expected_frames,
                         "updates_through": target})
    final = worker.call("status")
    if int(final["frames"]) != start_frames + n * transitions_per_item:
        raise RuntimeError("BEAN final training frame count does not reconcile")
    if int(final["updates"]) != start_updates + updates:
        raise RuntimeError("BEAN final update budget does not reconcile")
    return {"items": n, "transitions": n * transitions_per_item,
            "gradient_updates": updates, "cohort_count": batches,
            "items_per_cohort_max": size, "receipts": receipts,
            "scope": "training-only; existing development history already inspected"}
