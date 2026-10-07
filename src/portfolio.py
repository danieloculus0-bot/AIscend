from __future__ import annotations

import math
from typing import Any

from .core import StateStore
from .game import score_game
from .research import ResearchDecider


def _empty_game() -> dict[str, Any]:
    return {
        "points": 0,
        "multiple": 0.0,
        "elapsed_days": 0.0,
        "next_milestone": "2X / POINT 1",
        "next_milestone_multiple": 2.0,
        "level": 1,
        "level_target": 100000.0,
        "level_progress": 0.0,
        "level_complete": False,
    }


def choose_execution_rail(
    base: dict[str, Any] | None,
    advanced: dict[str, Any] | None,
) -> str:
    """Choose one funded live rail for the next autonomous cycle.

    This does not move money between rails. Manual bridge moves remain explicit.
    It only prevents two independent strategy loops from competing for the same
    experiment.
    """
    base = base or {}
    advanced = advanced or {}

    base_net = float(base.get("net_liquidation") or 0.0)
    advanced_net = float(advanced.get("net_liquidation") or 0.0)

    base_decision = ResearchDecider().decide(base) if base else None
    base_score = float((base.get("research") or {}).get("composite_score") or 0.0)

    radar = advanced.get("market_radar") or {}
    best = radar.get("best_long") or {}
    weakest = radar.get("weakest_held") or {}

    # Exits beat entries. Protecting the bankroll is a portfolio decision.
    if (
        base_decision is not None
        and base_decision.action == "SELL"
        and bool(base.get("positions") or {})
    ):
        return "base"
    if weakest:
        weakest_product = str(weakest.get("product") or "").upper()
        weakest_score = float(weakest.get("score") or 0.0)
        if weakest_product in (advanced.get("positions") or {}) and weakest_score <= -0.14:
            return "advanced"

    base_cash = float(base.get("cash") or 0.0)
    base_positions = base.get("positions") or {}
    base_buy = bool(
        base_decision is not None
        and base_decision.action == "BUY"
        and base_cash > 0.0
    )
    base_sell = bool(
        base_decision is not None
        and base_decision.action == "SELL"
        and bool(base_positions)
    )
    advanced_buy = False
    advanced_score = float(
        best.get("decision_score")
        or best.get("score")
        or radar.get("score")
        or 0.0
    )
    if best:
        product_id = str(best.get("product") or "").upper()
        _, _, quote = product_id.rpartition("-")
        quote_cash = float((advanced.get("cash_by_quote") or {}).get(quote, 0.0))
        advanced_buy = (
            advanced_score >= 0.18
            and float(best.get("spread_bps") or 0.0) <= 50.0
            and float(best.get("volume_ratio") or 0.0) >= 0.40
            and quote_cash > 0.0
        )

    if base_buy and advanced_buy:
        return "advanced" if advanced_score > base_score else "base"
    if advanced_buy:
        return "advanced"
    if base_buy:
        return "base"

    # No executable trade. Journal the HOLD on the rail that actually contains
    # more of the bankroll instead of repeatedly polling an empty rail.
    return "advanced" if advanced_net > base_net else "base"


def combine_portfolio(
    *,
    base: dict[str, Any] | None,
    advanced: dict[str, Any] | None,
    active_rail: str,
    game_store: StateStore,
) -> dict[str, Any]:
    """Build the one-bankroll view used by AIS-0, dashboard and monitor."""
    base = base or {}
    advanced = advanced or {}
    active = advanced if active_rail == "advanced" else base
    other = base if active_rail == "advanced" else advanced

    base_net = float(base.get("net_liquidation") or 0.0)
    advanced_net = float(advanced.get("net_liquidation") or 0.0)
    net = base_net + advanced_net

    start_text = game_store.get_meta("live_starting_value")
    if start_text is None and net > 0.01:
        start = net
        game_store.set_meta("live_starting_value", f"{start:.10f}")
    elif start_text is not None:
        start = float(start_text)
    else:
        start = 0.0

    game = score_game(game_store, net, start) if start > 0 else _empty_game()

    positions: dict[str, dict[str, Any]] = {}
    prices: dict[str, float] = {}
    for label, snap in (("BASE", base), ("ADV", advanced)):
        for symbol, position in (snap.get("positions") or {}).items():
            display = f"{label}:{symbol}"
            positions[display] = {**dict(position), "rail": label.lower()}
            prices[display] = float((snap.get("prices") or {}).get(symbol) or 0.0)

    research = dict(active.get("research") or {})
    # Human Weather is global context. Keep it visible while Advanced is the
    # execution rail instead of blanking the brain panel.
    if active_rail == "advanced":
        base_human = ((base.get("research") or {}).get("human") or {})
        if base_human:
            research["human"] = base_human
            research["global_context_score"] = float(
                (base.get("research") or {}).get("composite_score") or 0.0
            )

    listing = advanced.get("listing_sentinel") or active.get("listing_sentinel") or {}
    bean = base.get("bean") or advanced.get("bean") or {}

    cash = float(base.get("cash") or 0.0) + float(advanced.get("cash") or 0.0)
    market_value = float(base.get("market_value") or 0.0) + float(
        advanced.get("market_value") or 0.0
    )

    result = dict(active)
    result.update(
        {
            "cash": cash,
            "starting_cash": start,
            "positions": positions,
            "prices": prices,
            "market_value": market_value,
            "net_liquidation": net,
            "pnl": (net - start) if start else 0.0,
            "multiple": (net / start) if start else math.nan,
            "wallet_address": "Linked Base + Coinbase Advanced",
            "network": "linked-live",
            "game": game.as_dict() if hasattr(game, "as_dict") else game,
            "research": research,
            "bean": bean,
            "listing_sentinel": listing,
            "execution_rail": active_rail,
            "portfolio": {
                "complete": bool(base) and bool(advanced),
                "base_net": base_net,
                "advanced_net": advanced_net,
                "total_net": net,
                "active_rail": active_rail,
                "other_rail_net": float(other.get("net_liquidation") or 0.0),
            },
        }
    )
    return result
