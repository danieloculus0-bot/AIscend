"""Pure BEAN portfolio strategy selection, powered by observed adaptive rewards.

The selector cannot fake rewards or submit orders. It ranks independently
trained products by their current learned action value net of switching costs.
"""
from __future__ import annotations

import math

MAX_MARKETS_PER_CYCLE = 14
ROTATION_COST_BPS = 16.0  # 2 trades plus modeled adverse fills, not a safety limit


def assess_opportunities(growth_gate, radar, snapshot, *, max_markets=MAX_MARKETS_PER_CYCLE):
    """Train/assess candidates even when available quote cash is zero.

    Returns an auditable ranked set and a proposed portfolio target. The
    calling AIscend engine will execute and record its own actual trades.
    """
    rows = snapshot.get("asset_universe") or {}
    scored = rows.get("scored") or rows.get("buy_top") or rows.get("top") or []
    tradable = set((snapshot.get("tradable_products") or []))
    cash = snapshot.get("cash_by_quote") or {}
    held = snapshot.get("positions") or {}
    market_rows = (snapshot.get("market_radar") or {}).get("candidates") or []
    if market_rows:
        scored = market_rows
    candidates = {}
    for item in scored:
        product = str(item.get("product") or "").upper()
        if product and (not tradable or product in tradable):
            candidates[product] = item
    for product in held:
        candidates.setdefault(product, {"product": product})

    def score(item):
        return float(item.get("decision_score") if item.get("decision_score") is not None
                     else item.get("score") or 0)
    # Rotating universe is already broad. Allocate live evaluation calls to
    # the highest live candidates and all held positions.
    ranked = sorted(candidates.values(), key=score, reverse=True)
    focus = {str(item["product"]): item for item in ranked[:max_markets]}
    for product in held:
        focus[product] = candidates[product]
    assessed = []
    for product, item in focus.items():
        if product not in tradable and tradable:
            continue
        if not product.endswith(("-USD", "-USDC")):
            continue
        # Existing market scanner reports spread and volume. Hold positions
        # still get assessed regardless of scan freshness.
        spread = float(item.get("spread_bps") or 0)
        volume = float(item.get("volume_ratio") or 1)
        try:
            result = growth_gate.assess(product)
        except Exception as exc:
            result = {"status": "DATA_UNAVAILABLE",
                      "reason": f"{type(exc).__name__}: {str(exc)[:90]}"}
        if result.get("trade_effect") != "ADOPTED_LONG_CASH":
            continue
        q = result.get("chosen_action_scores_bps") or {}
        action = result.get("learned_strategy")
        learned_bps = float(q.get(action, 0))
        if not math.isfinite(learned_bps):
            continue
        target = int(result.get("target_exposure") or 0)
        trade_cost = (max(0, spread) / 2.0 + 7.0)
        net_edge = learned_bps - trade_cost if target else 0.0
        # No extrapolation of paper equity into wealth forecasts.
        assessed.append({"product": product,
                         "target_exposure": target, "strategy": action,
                         "reward_updates": result.get("reward_updates", 0),
                         "forward_bars": result.get("forward_bars", 0),
                         "behavior_revisions": result.get("behavior_revisions", 0),
                         "expected_reward_bps": round(learned_bps, 4),
                         "net_edge_bps": round(net_edge, 4),
                         "radar_score": score(item),
                         "spread_bps": spread,
                         "volume_ratio": volume,
                         "already_held": product in held,
                         "cash_available": cash.get(product.rsplit("-", 1)[-1], 0.0)})
    longs = [x for x in assessed if x["target_exposure"] and
             x["volume_ratio"] >= .4 and x["spread_bps"] <= 50]
    longs.sort(key=lambda x: (x["net_edge_bps"], x["radar_score"]), reverse=True)
    winner = longs[0] if longs else None
    return {"winner": winner, "ranked": assessed,
            "evaluated": len(assessed), "universe_candidates": len(candidates),
            "held_markets": list(held),
            "selection_reason": "highest_net_learned_reward_after_costs"}
