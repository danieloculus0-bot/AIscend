"""Cost-aware, evidence-labelled spot rotation planning.

Pure functions only. Expected growth is a forward heuristic derived from the
learner's *paper* bar reward; it is not calibrated future return or guaranteed
profit. Execution must check actual Coinbase availability and previews.
"""
from __future__ import annotations

from math import isfinite


def maturity_minutes(row: dict) -> int:
    """Provisional horizon from observed multi-timeframe price persistence."""
    r5 = float(row.get("return_5m") or 0.0)
    r1 = float(row.get("return_1h") or 0.0)
    r6 = float(row.get("return_6h") or 0.0)
    if r5 > 0 and r1 > 0 and r6 > 0:
        return 360
    if r5 > 0 and r1 > 0:
        return 60
    if r5 > 0:
        return 15
    return 5


def trade_cost_bps(spread_bps: float, taker_fee_bps: float) -> float:
    """One market order: estimated taker fee plus half quoted bid/ask spread."""
    return max(0.0, taker_fee_bps) + max(0.0, spread_bps) * 0.5


def score_route(candidate: dict, *, taker_fee_bps: float = 60.0,
                bridge: bool = False, incumbent_spread_bps: float = 0.0,
                selling_incumbent: bool = False,
                bridge_spread_bps: float = 0.0) -> dict:
    """Compare a possible position against *all* entry/exit/conversion legs.

    Includes an eventual exit, even if no immediate incumbent must be sold.
    The modeled reward is a per-5m-paper-bar estimate; the horizon multiplier
    is a *hypothesis* and must be audited against realized outcomes.
    """
    row = dict(candidate)
    horizon = maturity_minutes(row)
    reward = float(row.get("reward_bps") or 0.0)
    if not isfinite(reward):
        raise ValueError("Non-finite learner reward")
    bars = min(12, max(1, horizon // 5))
    estimated_growth = reward * bars
    spread = max(0.0, float(row.get("spread_bps") or 0.0))
    entry = trade_cost_bps(spread, taker_fee_bps)
    eventual_exit = trade_cost_bps(spread, taker_fee_bps)
    conversion = trade_cost_bps(bridge_spread_bps, taker_fee_bps) if bridge else 0.0
    sell_current = trade_cost_bps(incumbent_spread_bps, taker_fee_bps) if selling_incumbent else 0.0
    total = entry + eventual_exit + conversion + sell_current
    row.update({
        "maturity_minutes": horizon,
        "estimated_growth_bps": round(estimated_growth, 5),
        "total_route_cost_bps": round(total, 5),
        "net_projected_bps": round(estimated_growth - total, 5),
        "conversion_required": bool(bridge),
        "selling_incumbent": bool(selling_incumbent),
        "model": "unvalidated_paper_reward_x_horizon",
        "observed_win_rate": row.get("observed_win_rate"),
        "observed_samples": int(row.get("observed_samples") or 0),
    })
    return row


def bridge_order(from_quote: str, to_quote: str, tradable) -> dict | None:
    """Direct USD/USDC bridge, *only* when Coinbase lists the spot pair.

    USDC-USD SELL converts USDC into USD; BUY converts USD into USDC.
    Trading is still a normal Coinbase order, with its own preview and fees.
    """
    allowed = set(tradable or ())
    if "USDC-USD" not in allowed:
        return None
    if from_quote == "USDC" and to_quote == "USD":
        return {"product": "USDC-USD", "side": "SELL"}
    if from_quote == "USD" and to_quote == "USDC":
        return {"product": "USDC-USD", "side": "BUY"}
    return None
