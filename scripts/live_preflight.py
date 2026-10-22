"""Read-only Coinbase Advanced readiness check. Never places an order.

Usage:
  python -m scripts.live_preflight

This validates the *installed* program's credentials, tradability, stored
order-reconciliation state, product-rule minimums and recent decision.
A green result means at least one screened USD/USDC route is sized; it does
not prove order-preview approval, actual fills, or profitability.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import sqlite3
from pathlib import Path
from typing import Any

from src.core import app_data_dir
from src.bean_rotation_economics import bridge_order
from src.wallets.advanced_trade import AdvancedTradeSpot, AdvancedTradeVault


def _read_metadata(db: Path) -> dict[str, str]:
    if not db.is_file():
        return {}
    try:
        with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=5)) as con:
            return {
                str(key): str(value)
                for key, value in con.execute(
                    "SELECT key,value FROM meta WHERE key IN "
                    "('pending_coinbase_order','primary_trade_plan_latest',"
                    "'last_coinbase_submission_state')"
                )
            }
    except sqlite3.Error as exc:
        raise RuntimeError(f"Cannot inspect existing execution journal: {exc}") from exc


def _funded_executable_pairs(funded_assets: set[str], product_ids: tuple[str, ...]) -> list[str]:
    """Find reachable Coinbase spot markets using the live engine's bridge rule.

    A USD/USDC balance is useful only when the account has a market quoted in
    that currency, or the actual tradable USDC-USD conversion pair exists.
    A non-dollar holding can be liquidated only through its own tradable pair.
    The conversion-only pair is not itself an investable target market.
    """
    tradable = {str(product).upper() for product in product_ids}
    funded_quotes = funded_assets & {"USD", "USDC"}
    reachable = []
    for product in sorted(tradable):
        base, separator, quote = product.rpartition("-")
        if (not separator or not base or quote not in {"USD", "USDC"}
                or product == "USDC-USD"):
            continue
        if (base in funded_assets or quote in funded_assets
                or any(bridge_order(source, quote, tradable)
                       for source in funded_quotes)):
            reachable.append(product)
    return reachable


# Limit authenticated product-rule lookups during read-only relaunch checks.
# A capped/incomplete probe must NEVER be interpreted as an all-clear.
MAX_PRODUCT_RULE_CHECKS = 12


def _positive_amount(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and amount > 0 else Decimal(0)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _usable_size(amount: Decimal, rules: dict, *, side: str) -> bool:
    """Check spendable funds after the same venue increment rounding as live execution."""
    key = "quote" if side == "BUY" else "base"
    try:
        minimum = Decimal(str(rules[key + "_min_size"]))
        increment = Decimal(str(rules[key + "_increment"]))
        if (not minimum.is_finite() or minimum < 0 or
                not increment.is_finite() or increment <= 0):
            return False
        rounded = (amount / increment).to_integral_value(rounding=ROUND_DOWN) * increment
        return rounded > 0 and rounded >= minimum
    except (KeyError, InvalidOperation, TypeError, ValueError, ArithmeticError):
        return False


def _sized_funded_pairs(rail, product_ids: tuple[str, ...],
                        funded_pairs: list[str], balances: dict[str, Decimal]):
    """Bounded no-order lookup of Coinbase product increments and minimums.

    This is a rough sizing check, NOT a preview, fee quote, authorization to
    trade, or a promise about the current order book. The exchange's live
    preview still decides whether an actual order is valid.
    """
    getter = getattr(rail, "product", None)
    if not callable(getter):
        return [], 0, ["PRODUCT_RULES_UNAVAILABLE"], False
    listed = set(product_ids)
    cache = {}
    errors = set()
    lookups = 0
    capped = False

    def rules(product):
        nonlocal lookups, capped
        if product in cache:
            return cache[product]
        if lookups >= MAX_PRODUCT_RULE_CHECKS:
            capped = True
            return None
        lookups += 1
        try:
            value = getter(product)
            if not isinstance(value, dict):
                raise ValueError("Invalid product-rule payload")
            cache[product] = value
            return value
        except Exception:
            errors.add("PRODUCT_RULES_UNAVAILABLE")
            cache[product] = None
            return None

    # Prioritize held assets and directly funded quotes, since those require
    # only one leg and avoid unnecessary API calls to unrelated markets.
    ordered = sorted(
        funded_pairs,
        key=lambda p: (
            0 if p.rpartition("-")[0] in balances
            else 1 if p.rpartition("-")[2] in balances else 2,
            p))
    sized = []
    for product in ordered:
        base, _, quote = product.rpartition("-")
        rule = rules(product)
        if rule is None:
            continue
        if (base in balances and
                _usable_size(balances[base], rule, side="SELL")):
            sized.append(product)
            break
        if (quote in balances and
                _usable_size(balances[quote], rule, side="BUY")):
            sized.append(product)
            break
        for source in ("USD", "USDC"):
            amount = balances.get(source, Decimal(0))
            conversion = bridge_order(source, quote, listed)
            if amount <= 0 or not conversion:
                continue
            bridge_rule = rules(conversion["product"])
            if bridge_rule is None or not _usable_size(
                    amount, bridge_rule, side=conversion["side"]):
                continue
            # The USD/USDC conversion is approximately par, but a conservative
            # 2% reserve must cover both potential fee/spread changes. This
            # DOES NOT replace the real Coinbase market-order preview.
            if _usable_size(amount * Decimal("0.98"), rule, side="BUY"):
                sized.append(product)
                errors.add("CROSS_QUOTE_ORDER_SIZE_ESTIMATED")
                break
        if sized:
            break
    if capped:
        errors.add("PRODUCT_RULE_PROBE_LIMIT_REACHED")
    return sized, lookups, sorted(errors), capped


def inspect_advanced(*, vault=None, rail=None, db_path: Path | None = None) -> dict[str, Any]:
    vault = vault or AdvancedTradeVault()
    state = {"execution_rail": "coinbase-advanced", "checked_live_orders": False,
             "placed_orders": False, "read_only": True,
             "exchange_access": False, "execution_ready": False,
             "blockers": [], "warnings": []}
    if not vault.configured():
        state["blockers"].append("COINBASE_ADVANCED_CREDENTIALS_MISSING")
        return state
    rail = rail or AdvancedTradeSpot(vault=vault)
    try:
        status = rail.status()
        state["exchange_access"] = True
    except Exception as exc:
        state["blockers"].append("COINBASE_ACCOUNT_QUERY_FAILED")
        state["account_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        return state
    state["can_view"] = bool(status.can_view)
    state["can_trade"] = bool(status.can_trade)
    state["tradable_products"] = int(status.tradable_spot_products)
    balances = {
        str(a.currency).upper(): _positive_amount(a.available)
        for a in status.balances if _positive_amount(a.available) > 0
    }
    assets = sorted(balances)
    state["available_asset_symbols"] = assets
    if not state["can_view"] or not state["can_trade"]:
        state["blockers"].append("COINBASE_KEY_MISSING_VIEW_OR_TRADE")
    if not status.product_ids:
        state["blockers"].append("NO_MARKET_ORDER_ELIGIBLE_PRODUCTS")
    funded_pairs = _funded_executable_pairs(
        set(assets), tuple(status.product_ids))
    state["funded_executable_pairs"] = funded_pairs
    if not funded_pairs:
        state["blockers"].append("NO_FUNDED_EXECUTABLE_SPOT_ROUTE")
    sized, checks, warnings, limited = _sized_funded_pairs(
        rail, tuple(status.product_ids), funded_pairs, balances)
    state["sized_funded_pairs"] = sized
    state["order_size_rule_checks"] = checks
    state["warnings"].extend(warnings)
    if funded_pairs and not sized:
        state["blockers"].append("NO_ORDER_SIZE_ELIGIBLE_FUNDED_ROUTE")
    if limited and not sized:
        state["blockers"].append("ORDER_SIZE_CHECK_INCOMPLETE")
    meta = _read_metadata(db_path or app_data_dir() / "live-advanced.db")
    pending_raw = meta.get("pending_coinbase_order")
    if pending_raw:
        try:
            pending = json.loads(pending_raw)
        except (ValueError, TypeError):
            pending = {}
            state["blockers"].append("PENDING_ORDER_JOURNAL_CORRUPTED")
        state["pending_order"] = bool(pending_raw)
        state["pending_product"] = str(pending.get("product") or "") or None
        state["blockers"].append("UNRECONCILED_COINBASE_SUBMISSION")
    else:
        state["pending_order"] = False
    plan_raw = meta.get("primary_trade_plan_latest")
    if plan_raw:
        try:
            plan = json.loads(plan_raw)
            state["last_decision"] = plan.get("decision")
            state["last_execution_status"] = plan.get("execution_status")
            state["last_evaluated_at"] = plan.get("evaluated_at")
        except (ValueError, TypeError):
            state["warnings"].append("LAST_TRADE_PLAN_NOT_PARSEABLE")
    else:
        state["warnings"].append("NO_PRIOR_ADVANCED_DECISION_FOUND")
    state["execution_ready"] = not state["blockers"]
    state["note"] = ("Read-only account and journal preflight only; "
                     "no Coinbase order submission, order preview, "
                     "fill verification or profitability demonstration performed. "
                     "Product-rule sizing is read-only and estimated for bridged quotes.")
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON report destination")
    args = parser.parse_args(argv)
    report = inspect_advanced()
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if report["execution_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
