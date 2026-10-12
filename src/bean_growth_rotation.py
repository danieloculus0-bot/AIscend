"""Rank assets by feedback-learned strategy reward, not fixed scanner rank.

Pure research/selection logic. Execution remains in AdvancedSpotEngine, which
uses its existing order methods and journal. No wallet or exchange imports.
"""
from __future__ import annotations

from math import isfinite

SCAN_LIMIT = 8
ROTATION_BPS = 18.0


def evaluate_candidates(growth_gate, snapshot, *, limit=SCAN_LIMIT):
    universe = snapshot.get("asset_universe") or {}
    ranked = universe.get("scored") or universe.get("buy_top") or universe.get("top") or []
    tradable = set(snapshot.get("tradable_products") or ())
    positions = snapshot.get("positions") or {}
    seen = set()
    candidates = []
    for item in sorted(ranked, key=lambda x: float(
            x.get("decision_score") if x.get("decision_score") is not None
            else x.get("score") or 0.0), reverse=True):
        product = str(item.get("product") or "").upper()
        if (not product or product in seen or
                (tradable and product not in tradable) or
                not product.endswith(("-USD", "-USDC"))):
            continue
        candidates.append((product, item))
        seen.add(product)
        if len(candidates) >= limit:
            break
    # Explicitly evaluate owned assets even if absent from the scanner.
    for product in positions:
        if product not in seen and (not tradable or product in tradable):
            candidates.append((product, {"product": product}))
            seen.add(product)

    results = []
    for product, item in candidates:
        try:
            evidence = growth_gate.assess(product)
        except Exception as exc:
            evidence = {"status": "DATA_UNAVAILABLE",
                        "reason": type(exc).__name__}
        if evidence.get("trade_effect") != "ADOPTED_LONG_CASH":
            continue
        action = str(evidence.get("learned_strategy") or "")
        scores = evidence.get("chosen_action_scores_bps") or {}
        reward = float(scores.get(action, 0.0))
        if not isfinite(reward):
            continue
        spread = float(item.get("spread_bps") or 0.0)
        volume = float(item.get("volume_ratio") or 1.0)
        exposure = bool(evidence.get("target_exposure"))
        # Learned reward is measured per synthetic paper bar and is NOT a
        # future-profit forecast. Use it only to rank competing policies.
        cost_bps = max(0.0, spread) * .5 + 7
        edge = reward - cost_bps if exposure else 0.0
        results.append({
            "product": product,
            "action": action,
            "target_exposure": int(exposure),
            "reward_bps": round(reward, 5),
            "edge_bps": round(edge, 5),
            "spread_bps": spread,
            "volume_ratio": volume,
            "forward_bars": evidence.get("forward_bars", 0),
            "reward_updates": evidence.get("reward_updates", 0),
            "behavior_revisions": evidence.get("behavior_revisions", 0),
            "already_held": product in positions,
        })
    longs = [r for r in results if r["target_exposure"]
             and r["volume_ratio"] >= .4 and r["spread_bps"] <= 50]
    longs.sort(key=lambda r: (-r["edge_bps"], r["product"]))
    return {"best_long": longs[0] if longs else None,
            "evaluated": results, "universe_count": len(ranked),
            "assessed_count": len(candidates), "rotation_cost_bps": ROTATION_BPS}
