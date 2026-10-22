"""Authenticated Coinbase execution evidence supplied to BEAN's live selector.

No API keys, trade submissions, or hypothetical fills. Appends unique provider
fills to local SQLite and reports what is verified versus unknown. Account equity
changes are NOT profit until deposits, withdrawals and transfers are reconciled.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import math
import time


def _money(value):
    try:
        amount = float(value)
        return amount if math.isfinite(amount) and amount >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _positive(value):
    amount = _money(value)
    return amount if amount is not None and amount > 0 else None


def _signed_finite(value):
    try:
        amount = float(value)
        return amount if math.isfinite(amount) else None
    except (ValueError, TypeError, OverflowError):
        return None


def capital_snapshot(snapshot):
    """Preserve the original observed starting equity, not a synthetic seed."""
    complete = bool(snapshot.get("valuation_complete", False))
    current = _money(snapshot.get("net_liquidation"))
    starting = _money(snapshot.get("starting_cash"))
    if starting == 0:
        starting = None
    if not complete:
        current = None
    return {
        "source": "authenticated_coinbase_account_snapshot",
        "starting_equity_usd": starting,
        "current_equity_usd": current,
        "available_quote_cash": dict(snapshot.get("cash_by_quote") or {}),
        "unpriced_assets": dict(snapshot.get("unpriced_balances") or {}),
        "valuation_complete": complete,
        "equity_change_usd": round(current - starting, 8)
            if starting is not None and current is not None else None,
        "equity_change_is_profit": False,
        "cash_flows_reconciled": False,
        "baseline_description": "first_observed_value_not_original_deposit",
        "reason": ("External deposits, withdrawals, transfers and fiat/stablecoin "
                   "conversions have not been reconciled; equity change is "
                   "not net trading profit."),
    }


def _normalize_fill(raw):
    if not isinstance(raw, dict):
        raise ValueError("Coinbase fill is not an object")
    identity = str(raw.get("entry_id") or raw.get("trade_id") or "").strip()
    product = str(raw.get("product_id") or "").upper()
    side = str(raw.get("side") or "").upper()
    if (not identity or "-" not in product
            or side not in ("BUY", "SELL")):
        raise ValueError("Missing verified Coinbase fill identity, side or spot pair")
    size = _positive(raw.get("size"))
    price = _positive(raw.get("price"))
    fee = _money(raw.get("commission"))
    if size is None or price is None or fee is None:
        raise ValueError("Coinbase fill size, price or commission is incomplete")
    stamp = str(raw.get("trade_time") or "").strip()
    if not stamp:
        raise ValueError("Coinbase fill timestamp missing")
    # A provider fee can only be subtracted from quote-denominated P&L when
    # its denomination is explicitly confirmed.
    # Coinbase Markets trading rule 1.91 charges Advanced spot fees in
    # the quote asset. The fill API may omit a fee currency field.
    # An explicit conflicting currency is never silently converted.
    fee_currency = str(raw.get("commission_currency") or
                       raw.get("fee_currency") or
                       product.rsplit("-", 1)[1]).upper()
    return {
        "fill_id": identity, "product": product, "side": side,
        "size": str(Decimal(str(size))), "price": str(Decimal(str(price))),
        "fee": str(Decimal(str(fee))), "fee_currency": fee_currency,
        "trade_time": stamp,
        "order_id": str(raw.get("order_id") or ""),
        "source": "authenticated_coinbase_advanced_get_fills",
    }


def preserve_fills(store, response, *, clock=time.time):
    """Idempotently retain all observed fills, including an incomplete page run."""
    if not isinstance(response, dict) or not isinstance(response.get("fills"), list):
        raise ValueError("Coinbase fills response missing list")
    store.conn.execute("""CREATE TABLE IF NOT EXISTS bean_verified_fills (
        fill_id TEXT PRIMARY KEY, evidence TEXT NOT NULL,
        observed_at REAL NOT NULL)""")
    normalized = [_normalize_fill(item) for item in response["fills"]]
    with store.conn:
        for fill in normalized:
            payload = json.dumps(fill, sort_keys=True)
            previous = store.conn.execute(
                "SELECT evidence FROM bean_verified_fills WHERE fill_id=?",
                (fill["fill_id"],)).fetchone()
            if previous is not None and previous["evidence"] != payload:
                raise ValueError("Coinbase returned conflicting evidence for a fill ID")
            if previous is None:
                store.conn.execute(
                    "INSERT INTO bean_verified_fills(fill_id,evidence,observed_at) "
                    "VALUES(?,?,?)", (fill["fill_id"], payload, float(clock())))
    if response.get("complete") is True:
        store.set_meta("bean_fills_last_complete_at", str(float(clock())))
    else:
        store.set_meta("bean_fills_last_complete_at", "")
    store.set_meta("bean_fills_last_attempt_at", str(float(clock())))
    return len(normalized)


def execution_history(store, *, fresh_seconds=900, clock=time.time):
    """Never declare a partial or stale exchange history complete."""
    store.conn.execute("""CREATE TABLE IF NOT EXISTS bean_verified_fills (
        fill_id TEXT PRIMARY KEY, evidence TEXT NOT NULL,
        observed_at REAL NOT NULL)""")
    last = _money(store.get_meta("bean_fills_last_complete_at"))
    fresh = last is not None and 0 <= float(clock()) - last <= fresh_seconds
    rows = [json.loads(row["evidence"]) for row in
            store.conn.execute("SELECT evidence FROM bean_verified_fills").fetchall()]
    rows.sort(key=lambda x: (x["trade_time"], x["fill_id"]))
    result = {
        "status": "COMPLETE" if fresh else "UNVERIFIED_OR_STALE",
        "last_full_provider_read_at": last,
        "fill_count_retained": len(rows),
        "history_fully_reconciled": fresh,
        "products": {},
        "recent_trades": rows[-40:],
        "note": "Trade records are provider fills, never order acknowledgements.",
    }
    if not fresh:
        return result
    balances = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    outcome = defaultdict(lambda: {"closed_sells": 0, "wins": 0,
                                   "realized_quote_pnl": Decimal("0"),
                                   "sold_cost": Decimal("0")})
    invalid = set()
    for row in rows:
        p = row["product"]
        q = p.rsplit("-", 1)[1]
        # Missing or differently denominated fees invalidate *net* evidence
        # on that product; other products may still have reconciled histories.
        if row["fee_currency"] != q:
            invalid.add(p)
            continue
        size, price, fee = (Decimal(row[key]) for key in ("size", "price", "fee"))
        units, cost = balances[p]
        if row["side"] == "BUY":
            balances[p] = [units + size, cost + size * price + fee]
            continue
        if size > units or units <= 0:
            invalid.add(p)
            continue
        allocated = cost * size / units
        received = size * price - fee
        pnl = received - allocated
        v = outcome[p]
        v["closed_sells"] += 1
        v["wins"] += int(pnl > 0)
        v["realized_quote_pnl"] += pnl
        v["sold_cost"] += allocated
        balances[p] = [units - size, cost - allocated]
    for p in sorted(set(balances) | set(outcome) | invalid):
        v = outcome[p]
        good = p not in invalid and v["closed_sells"] > 0
        value = v["realized_quote_pnl"]
        cost = v["sold_cost"]
        result["products"][p] = {
            "status": "VERIFIED_NET_CLOSED_TRADES" if good else "UNVERIFIED",
            "closed_sells": v["closed_sells"] if good else 0,
            "wins": v["wins"] if good else None,
            "realized_net_quote": float(value) if good else None,
            "matched_sold_cost_quote": float(cost) if good else None,
            "return_on_matched_cost_bps": round(float(value / cost * 10000), 3)
                if good and cost > 0 else None,
            "quote": p.rsplit("-", 1)[1],
            "scope": "matched_closed_execution_fills_only",
        }
    return result


def evaluate_account(store, snapshot, rail, *, clock=time.time,
                     refresh_seconds=300):
    """Read-only fetch, durable ledger, reported sync errors; no trade calls."""
    capital = capital_snapshot(snapshot)
    last = _money(store.get_meta("bean_fills_last_attempt_at"))
    now = float(clock())
    reader = getattr(rail, "account_fills", None)
    status = "NOT_QUERIED"
    if not callable(reader):
        status = "AUTHENTICATED_FILL_READER_UNAVAILABLE"
    elif last is None or now - last >= refresh_seconds or now < last:
        # Throttle even unsuccessful reads; never burn rate limits per scan.
        store.set_meta("bean_fills_last_attempt_at", str(now))
        try:
            response = reader()
            preserve_fills(store, response, clock=clock)
            status = "PROVIDER_HISTORY_COMPLETE" if response.get("complete") else "PROVIDER_HISTORY_PARTIAL"
        except Exception as exc:
            store.set_meta("bean_fills_last_complete_at", "")
            status = "HISTORY_READ_FAILED:" + type(exc).__name__
    else:
        status = "CACHED_HISTORY"
    return {
        "capital": capital,
        "execution": execution_history(store, clock=clock),
        "read_status": status,
        "realized_account_profit_verified": False,
    }
