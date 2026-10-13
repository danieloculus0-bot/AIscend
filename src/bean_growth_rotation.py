"""Rank assets by feedback-learned strategy reward, not fixed scanner rank.

Pure research/selection logic. Execution remains in AdvancedSpotEngine, which
uses its existing order methods and journal. No wallet or exchange imports.
"""
from __future__ import annotations

from math import isfinite
from concurrent.futures import ThreadPoolExecutor

# A cycle processes a bounded chunk of candles/API calls. The cursor rotates
# through the entire observed universe instead of permanently ignoring rank 9+.
# This is a throughput budget, not a restriction on which assets BEAN may trade.
EVALUATION_BATCH = 48


def evaluate_candidates(growth_gate, snapshot, *, limit=EVALUATION_BATCH, cursor=0):
    universe = snapshot.get("asset_universe") or {}
    ranked = universe.get("scored") or universe.get("buy_top") or universe.get("top") or []
    tradable = set(snapshot.get("tradable_products") or ())
    positions = snapshot.get("positions") or {}
    candidates = []
    ordered = sorted(ranked, key=lambda x: float(
        x.get("decision_score") if x.get("decision_score") is not None
        else x.get("score") or 0.0), reverse=True)
    eligible = []
    seen = set()
    for item in ordered:
        product = str(item.get("product") or "").upper()
        if (not product or product in seen or
                (tradable and product not in tradable) or
                not product.endswith(("-USD", "-USDC"))):
            continue
        eligible.append((product, item))
        seen.add(product)
    # Keep high-scoring candidates in every cycle while rolling across all
    # other observed products. Held positions always get assessed regardless.
    budget = max(2, int(limit))
    top_count = min(len(eligible), max(1, budget // 2))
    selected = eligible[:top_count]
    remainder = eligible[top_count:]
    rotation_count = min(len(remainder), budget - len(selected))
    if remainder:
        offset = int(cursor) % len(remainder)
        selected.extend(remainder[(offset + i) % len(remainder)]
                        for i in range(rotation_count))
    candidates.extend(selected)
    seen = {product for product, _ in candidates}
    # Explicitly evaluate owned assets even if absent from the scanner.
    for product in positions:
        if product not in seen and (not tradable or product in tradable):
            candidates.append((product, {"product": product}))
            seen.add(product)

    # Market data fetching happens outside the learner's SQLite lock.
    # Concurrent reads keep a broad evaluation from stalling each cycle.
    def assess_one(candidate):
        try:
            return growth_gate.assess(candidate[0])
        except Exception as exc:
            return {"status": "DATA_UNAVAILABLE",
                    "reason": type(exc).__name__}

    results = []
    if candidates:
        with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as pool:
            evidences = list(pool.map(assess_one, candidates))
    else:
        evidences = []
    for (product, item), evidence in zip(candidates, evidences):
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
    # Scores are already net of modeled spread/fees. Do not apply another
    # arbitrary spread or volume veto after scoring the opportunity.
    longs = [r for r in results if r["target_exposure"] and r["edge_bps"] > 0]
    longs.sort(key=lambda r: (-r["edge_bps"], r["product"]))
    return {"best_long": longs[0] if longs else None,
            "evaluated": results, "universe_count": len(ranked),
            "eligible_count": len(eligible),
            "assessed_count": len(candidates),
            "next_cursor": (int(cursor) + rotation_count) % max(1, len(remainder))}
