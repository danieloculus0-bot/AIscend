from __future__ import annotations

import math
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

from .core import StateStore


DEFAULT_WIN_TARGET = float(os.getenv("AISCEND_WIN_TARGET", "100000"))

MILESTONES = tuple((float(2**n), f"{2**n}X") for n in range(1, 21))


@dataclass(frozen=True)
class GameScore:
    points: int
    multiple: float
    elapsed_days: float
    target_value: float
    target_progress: float
    next_milestone: str
    next_milestone_multiple: float | None
    victory: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def set_win_target(store: StateStore, value: float) -> float:
    value = max(1.0, float(value))
    store.set_meta("game_win_target", f"{value:.2f}")
    return value


def get_win_target(store: StateStore) -> float:
    raw = store.get_meta("game_win_target")
    return float(raw) if raw else DEFAULT_WIN_TARGET


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

    target = get_win_target(store)
    target_progress = min(1.0, net / target) if target > 0 else 0.0
    victory = net >= target

    next_name = "FORTY ACRES"
    next_multiple: float | None = None
    for milestone_multiple, milestone_name in MILESTONES:
        if multiple < milestone_multiple:
            next_name = f"{milestone_name} / POINT {points + 1}"
            next_multiple = milestone_multiple
            break

    if next_multiple is None and not victory:
        next_multiple = target / start

    _record_crossed_milestones(store, multiple, now, started)

    return GameScore(
        points=points,
        multiple=multiple,
        elapsed_days=elapsed_days,
        target_value=target,
        target_progress=target_progress,
        next_milestone=next_name,
        next_milestone_multiple=next_multiple,
        victory=victory,
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
