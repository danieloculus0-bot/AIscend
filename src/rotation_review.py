"""BEAN opportunity-cost critic: side-effect-free assessment, never an order."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math


@dataclass(frozen=True)
class RotationReview:
    action: str
    held_product: str
    challenger_product: str | None
    held_score: float | None
    challenger_score: float | None
    improvement: float | None
    estimated_roundtrip_cost: float
    reason: str

    def as_dict(self):
        return asdict(self)


def _score(row):
    if not isinstance(row, dict):
        return None
    value = row.get("decision_score")
    if value is None:
        value = row.get("score")
    if value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def review_rotation(held_product, held_signal, challenger, *, entry_fee=0.004,
                    exit_fee=0.004, slippage=0.002, margin=0.05):
    """Compare evidence without assuming signal scores represent actual returns.

    This is a *priority* margin, not a forecast of profit. UNKNOWN scores hold.
    No execution can be triggered by this function.
    """
    held = _score(held_signal)
    proposed = _score(challenger)
    proposed_product = (str(challenger.get("product") or "").upper()
                        if isinstance(challenger, dict) else "")
    cost = entry_fee + exit_fee + 2 * slippage
    if not proposed_product or proposed_product == held_product.upper():
        return RotationReview("HOLD", held_product, proposed_product or None, held,
                              proposed, None, cost, "No distinct alternative")
    if held is None or proposed is None:
        return RotationReview("RESEARCH", held_product, proposed_product, held,
                              proposed, None, cost, "Incomplete signal evidence")
    spread = challenger.get("spread_bps")
    volume = challenger.get("volume_ratio")
    if spread is None or volume is None:
        return RotationReview("RESEARCH", held_product, proposed_product, held,
                              proposed, proposed - held, cost, "Missing execution quality")
    try:
        spread, volume = float(spread), float(volume)
    except (TypeError, ValueError, OverflowError):
        return RotationReview("RESEARCH", held_product, proposed_product, held,
                              proposed, proposed - held, cost, "Invalid market quality")
    if not math.isfinite(spread) or not math.isfinite(volume) or spread > 50 or volume < 0.4:
        return RotationReview("HOLD", held_product, proposed_product, held,
                              proposed, proposed - held, cost, "Execution quality gate failed")
    gap = proposed - held
    if gap < margin:
        return RotationReview("HOLD", held_product, proposed_product, held,
                              proposed, gap, cost, "Insufficient score improvement")
    return RotationReview("REVIEW", held_product, proposed_product, held,
                          proposed, gap, cost,
                          "Potential rotation: requires real fee/spread economics, "
                          "sufficient order size, out-of-sample confirmation and cooldown")
