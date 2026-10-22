"""Read-only Coinbase Advanced readiness check. Never places an order.

Usage:
  python -m scripts.live_preflight

This validates the *installed* program's credentials, tradability, stored
order-reconciliation state and recent decision. A green result means the
account is readable and operable; it does not prove fills or profitability.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.core import app_data_dir
from src.wallets.advanced_trade import AdvancedTradeSpot, AdvancedTradeVault


def _read_metadata(db: Path) -> dict[str, str]:
    if not db.is_file():
        return {}
    try:
        with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=5) as con:
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
    assets = [
        str(a.currency).upper() for a in status.balances
        if float(a.available) > 0
    ]
    state["available_asset_symbols"] = sorted(set(assets))
    if not state["can_view"] or not state["can_trade"]:
        state["blockers"].append("COINBASE_KEY_MISSING_VIEW_OR_TRADE")
    if not status.product_ids:
        state["blockers"].append("NO_MARKET_ORDER_ELIGIBLE_PRODUCTS")
    tradable_bases = {
        p.rpartition("-")[0] for p in status.product_ids
        if p.endswith(("-USD", "-USDC"))
    }
    if not ("USD" in assets or "USDC" in assets
            or any(asset in tradable_bases for asset in assets)):
        state["blockers"].append("NO_SPENDABLE_FUNDS_ON_SUPPORTED_SPOT_PAIR")
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
                     "no Coinbase order submission, fill verification or "
                     "profitability demonstration performed.")
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
