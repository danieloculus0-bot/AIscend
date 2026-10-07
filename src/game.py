from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any

from .core import StateStore


MILESTONES = tuple((float(2**n), f"{2**n}X") for n in range(1, 31))


@dataclass(frozen=True)
class GameScore:
    points: int
    multiple: float
    elapsed_days: float
    next_milestone: str
    next_milestone_multiple: float
    level: int
    level_target: float
    level_progress: float
    level_complete: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_game(
    store: StateStore,
    net_liquidation: float,
    starting_value: float,
) -> GameScore:
    now = time.time()
    started_raw = store.get_meta("game_started_at")
    if started_raw is None:
        store.set_meta("game_started_at", str(now))
        started = now
    else:
        started = float(started_raw)

    start = max(0.000001, float(starting_value))
    net = max(0.0, float(net_liquidation))
    multiple = net / start
    elapsed_days = max(0.0, (now - started) / 86400.0)

    # Exactly one point for each completed bankroll doubling.
    # 1x to <2x = 0 points, 2x to <4x = 1, 4x to <8x = 2, etc.
    points = max(0, math.floor(math.log2(max(multiple, 1.0))))

    next_multiple = float(2 ** (points + 1))
    next_name = f"{int(next_multiple)}X / POINT {points + 1}"

    _record_crossed_milestones(store, multiple, now, started)

    level = 1
    level_target = 100000.0
    level_progress = min(1.0, net / level_target)
    level_complete = net >= level_target

    if level_complete and store.get_meta("level_1_complete_at") is None:
        store.set_meta("level_1_complete_at", str(now))
        store.set_meta("level_1_complete_value", f"{net:.8f}")

    return GameScore(
        points=points,
        multiple=multiple,
        elapsed_days=elapsed_days,
        next_milestone=next_name,
        next_milestone_multiple=next_multiple,
        level=level,
        level_target=level_target,
        level_progress=level_progress,
        level_complete=level_complete,
    )


def _record_crossed_milestones(
    store: StateStore,
    multiple: float,
    now: float,
    started: float,
) -> None:
    for milestone_multiple, milestone_name in MILESTONES:
        key = f"milestone_{milestone_name.lower()}_at"
        if multiple >= milestone_multiple and store.get_meta(key) is None:
            store.set_meta(key, str(now))
            store.set_meta(
                f"milestone_{milestone_name.lower()}_elapsed_days",
                f"{(now - started) / 86400.0:.8f}",
            )
