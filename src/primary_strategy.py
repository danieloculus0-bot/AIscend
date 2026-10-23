"""AIscend's PRIMARY spot trading policy. BEAN does not vote or veto.

Three conventional, auditable price-action policies compete using only
current/previous publicly observed market metrics. Scores are hypotheses, not
calibrated predictions; full route costs and decision rationale are visible.
"""
from __future__ import annotations

import math
import time

from .bean_rotation_economics import bridge_order, score_route

POLICIES = ("trend", "breakout", "reversal")
DEFAULT_TAKER_BPS = 60.0
MAX_SIGNAL_AGE_SECONDS = 150.0
ROTATION_MARGIN_BPS = 12.0
TRAILING_DRAWDOWN_FRACTION = 0.08
SEVERE_DRAWDOWN_FRACTION = 0.12
ENTRY_LOSS_FRACTION = 0.08


def evidence_fresh(row: dict, *, max_age_seconds=MAX_SIGNAL_AGE_SECONDS) -> bool:
    """Reject expired timestamped signals. Legacy synthetic tests lack timestamps."""
    timestamp = row.get("scanned_at")
    if timestamp is None:
        return True
    try:
        age = time.time() - float(timestamp)
        return math.isfinite(age) and -5.0 <= age <= max_age_seconds
    except (TypeError, ValueError):
        return False


def incumbent_hold_net_bps(row: dict | None, target_minutes: int, *, taker_bps: float) -> float:
    """Compare HOLD and rotation on an equal horizon, net of future exit costs."""
    if not row or not evidence_fresh(row):
        return 0.0
    signals = proposals(row)
    if not signals:
        return 0.0
    spread = max(0.0, float(row.get("spread_bps") or 0.0))
    exit_cost = max(0.0, taker_bps) + 0.5 * spread
    minutes = max(1, int(target_minutes))
    return max(
        float(signal["gross_projection_bps"]) *
        min(1.0, minutes / max(1, int(signal["maturity_minutes"]))) - exit_cost
        for signal in signals
    )



def proposals(row: dict, *, enabled=POLICIES) -> list[dict]:
    product = str(row.get("product") or "").upper()
    if not product or "-" not in product:
        return []
    try:
        r5 = float(row.get("return_5m") or 0.0)
        r1 = float(row.get("return_1h") or 0.0)
        r6 = float(row.get("return_6h") or 0.0)
        # Absent or zero volume must not manufacture breakout/reversal confirmation.
        observed_volume = row.get("volume_ratio")
        vol = float(observed_volume) if observed_volume is not None else 0.0
        spread = float(row.get("spread_bps") or 0.0)
    except (TypeError, ValueError):
        return []
    if not all(math.isfinite(x) for x in (r5,r1,r6,vol,spread)):
        return []
    specs = []
    if "trend" in enabled and r5 > 0 and r1 > 0 and r6 > 0:
        # Momentum persistence heuristic, discounted for forecasting error.
        gross = (0.45*r1 + 0.35*r6 + 0.20*r5) * 10000 * .55
        specs.append(("trend", 360, gross))
    if "breakout" in enabled and r5 > 0 and r1 > 0 and vol >= 1.25:
        gross = (0.65*r5 + 0.35*r1)*10000*.65
        specs.append(("breakout", 60, gross))
    if "reversal" in enabled and r1 < -0.01 and r5 > 0 and vol >= 1.0:
        gross = (abs(r1)*.30 + r5*.25)*10000
        specs.append(("reversal", 15, gross))
    return [{
        "product": product,
        "action": name,
        "strategy": name,
        "maturity_minutes": horizon,
        "gross_projection_bps": round(max(0.0, gross), 5),
        "spread_bps": max(0.0, spread),
        "volume_ratio": vol,
        "return_5m": r5, "return_1h": r1, "return_6h": r6,
        "signal_source": "current_public_price_action_unvalidated_heuristic",
    } for name, horizon, gross in specs]


def learned_proposal(item: dict) -> dict | None:
    """Turn a READY BEAN Q-policy into a real costed entry candidate."""
    if not item.get("target_exposure") or not item.get("reward_available"):
        return None
    product = str(item.get("product") or "").upper()
    if not product or "-" not in product:
        return None
    try:
        reward = float(item["reward_bps"])
        spread = float(item.get("spread_bps") or 0)
    except (ValueError, TypeError, KeyError):
        return None
    if not all(math.isfinite(x) for x in (reward, spread)):
        return None
    r5 = float(item.get("return_5m") or 0)
    r1 = float(item.get("return_1h") or 0)
    r6 = float(item.get("return_6h") or 0)
    if r5 > 0 and r1 > 0 and r6 > 0:
        horizon = 360
    elif r5 > 0 and r1 > 0:
        horizon = 60
    elif r5 > 0:
        horizon = 15
    else:
        horizon = 5
    return {
        **item, "product": product,
        "strategy": "bean_q_" + str(item.get("action") or "learned"),
        "maturity_minutes": horizon,
        "gross_projection_bps": reward * min(12, max(1, horizon // 5)),
        "signal_source": "bean_q_feedback_unvalidated_forward_hypothesis",
    }


def verified_loss_lockout(account_history: dict | None, product: str) -> bool:
    """Conservative re-entry lockout based on authenticated closed trades.

    Applies to ALL primary and learned strategies, but only with current,
    account-scoped, complete Coinbase fill history. This is not an assertion
    that a previously losing coin must always lose.
    """
    history = account_history or {}
    execution = history.get("execution") or {}
    if (not history.get("account_scope_verified") or
            execution.get("status") != "COMPLETE"):
        return False
    item = (execution.get("products") or {}).get(product.upper()) or {}
    if item.get("status") != "VERIFIED_NET_CLOSED_TRADES":
        return False
    try:
        sells = int(item.get("closed_sells") or 0)
        cost_bps = float(item.get("return_on_matched_cost_bps"))
        pnl = float(item.get("realized_net_quote"))
        return (sells >= 3 and math.isfinite(cost_bps) and
                math.isfinite(pnl) and cost_bps < 0 and pnl < 0)
    except (TypeError, ValueError, OverflowError):
        return False


def select_plan(snapshot: dict, *, taker_bps=DEFAULT_TAKER_BPS,
                enabled=POLICIES, learned_candidates=None,
                account_history=None) -> dict:
    universe = snapshot.get("asset_universe") or {}
    ranked = list(universe.get("scored") or universe.get("buy_top") or universe.get("top") or [])
    learned_candidates = list(learned_candidates or [])
    learned_by_product = {
        str(item.get("product") or "").upper(): item
        for item in learned_candidates if isinstance(item, dict)
    }
    present = {str(item.get("product") or "").upper() for item in ranked}
    # A learned position must not be invisible merely because the public scan
    # omitted the instrument. Only live assessment-backed rows are included.
    ranked.extend(item for product, item in learned_by_product.items()
                  if product not in present and item.get("target_exposure"))
    tradable = set(snapshot.get("tradable_products") or ())
    # An explicit empty authenticated tradability list means no market is
    # executable. Only an absent field permits unscoped research fixtures.
    tradability_scoped = "tradable_products" in snapshot
    held = snapshot.get("positions") or {}
    cash = snapshot.get("cash_by_quote") or {}
    valuations = snapshot.get("balance_values_usd") or {}
    spreads = {str(r.get("product") or ""): float(r.get("spread_bps") or 0.0)
               for r in ranked}
    bridge_spread = spreads.get("USDC-USD", 10.0)
    quotes = set(k for k,v in cash.items() if float(v)>0)
    quotes.update(p.rpartition("-")[2] for p in held)
    diag = {"examined":len(ranked), "candidate_signals":0, "costed_routes":0,
            "skipped":{"no_signal":0,"untradable":0,"no_quote_path":0,
                       "negative_net_edge":0,"stale_signal":0,
                       "verified_historical_loss":0}, "routes":[], "winner":None}
    for row in ranked:
        if not evidence_fresh(row):
            diag["skipped"]["stale_signal"] += 1
            continue
        product = str(row.get("product") or "").upper()
        if tradability_scoped and product not in tradable:
            diag["skipped"]["untradable"] += 1
            continue
        if verified_loss_lockout(account_history, product):
            diag["skipped"]["verified_historical_loss"] += 1
            continue
        opts=proposals(row,enabled=enabled)
        learned = learned_proposal(learned_by_product.get(product) or {})
        if learned is not None:
            opts.append(learned)
        if not opts:
            diag["skipped"]["no_signal"] += 1
            continue
        if product in held:
            # Do not buy the same position again merely because signal repeats.
            continue
        diag["candidate_signals"] += len(opts)
        quote=product.rpartition("-")[2]
        valid=False
        for option in opts:
            for source_quote in quotes:
                bridge=source_quote!=quote
                if bridge and bridge_order(source_quote,quote,tradable) is None:
                    continue
                for source in [None,*held]:
                    if source and source.rpartition("-")[2]!=source_quote:
                        continue
                    if source is None and float(cash.get(source_quote,0))<=0:
                        continue
                    valid=True
                    qty=float(cash.get(source_quote,0)) if source is None else 0.0
                    if source:
                        base=source.rpartition("-")[0]
                        qty=float(valuations.get(base) or 0)
                        if qty<=0:
                            qty=(float(held[source].get("available_qty",
                                 held[source].get("qty",0)) or 0)
                                 * float((snapshot.get("prices") or {}).get(source) or 0))
                    if qty<=0:
                        continue
                    plan=score_route(option,taker_fee_bps=taker_bps,
                          bridge=bridge,bridge_spread_bps=bridge_spread,
                          selling_incumbent=bool(source),
                          incumbent_spread_bps=spreads.get(source,10.0))
                    plan.update({"source_quote":source_quote,
                                 "source_asset":source,
                                 "available_notional":round(qty,6),
                                 "estimated_gain_usd":round(
                                     qty*plan["net_projected_bps"]/10000,6)})
                    diag["costed_routes"]+=1
                    if plan["net_projected_bps"]<=0:
                        diag["skipped"]["negative_net_edge"]+=1
                        continue
                    diag["routes"].append(plan)
        if not valid:
            diag["skipped"]["no_quote_path"]+=1
    diag["routes"].sort(key=lambda r:r["estimated_gain_usd"],reverse=True)
    diag["routes"]=diag["routes"][:20]
    diag["winner"]=diag["routes"][0] if diag["routes"] else None
    return diag


def exit_reason(held_product: str, snapshot: dict, *,
                deadline_reached=False, observed_peak_price=None,
                current_mark_price=None, verified_entry_price=None,
                learned_exposure=None) -> str | None:
    """Live held-position exit policy, including BEAN cash and observed loss.

    No P/L is fabricated when Coinbase historical fill coverage is incomplete.
    The thresholds are real trading rules, not advisory notifications.
    """
    if learned_exposure is False:
        return "bean_q_policy_cash"
    rows = (snapshot.get("asset_universe") or {}).get("scored") or []
    row = next((r for r in rows
                if str(r.get("product") or "").upper() == held_product.upper()), None)
    try:
        mark = float(current_mark_price or 0)
        peak = float(observed_peak_price or 0)
        entry = float(verified_entry_price or 0)
        if not all(math.isfinite(x) for x in (mark, peak, entry)):
            mark = peak = entry = 0.0
    except (ValueError, TypeError, OverflowError):
        mark = peak = entry = 0.0
    if mark > 0 and entry > 0 and mark <= entry * (1 - ENTRY_LOSS_FRACTION):
        return "verified_entry_loss_limit"
    if mark > 0 and peak > 0 and mark <= peak:
        drop = 1 - mark / peak
        if drop >= SEVERE_DRAWDOWN_FRACTION:
            return "severe_observed_drawdown"
        if drop >= TRAILING_DRAWDOWN_FRACTION:
            # Peak loss is a valid signal even if public candles are missing.
            return "observed_trailing_drawdown"
    if row is None or not evidence_fresh(row):
        return "planned_maturity_exit_unobserved" if deadline_reached else None
    try:
        r5 = float(row.get("return_5m") or 0)
        r1 = float(row.get("return_1h") or 0)
        r6 = float(row.get("return_6h") or 0)
    except (ValueError, TypeError, OverflowError):
        return "planned_maturity_exit_unobserved" if deadline_reached else None
    if not all(math.isfinite(x) for x in (r5, r1, r6)):
        return "planned_maturity_exit_unobserved" if deadline_reached else None
    if r5 < -0.008 and r1 < 0:
        return "trend_invalidation"
    if r1 <= -0.015 and r6 <= -0.025:
        return "sustained_downtrend"
    if r1 <= -0.006 and r6 <= -0.02 and r5 <= 0:
        return "persistent_multi_hour_weakness"
    if deadline_reached:
        return "planned_maturity_exit"
    return None
