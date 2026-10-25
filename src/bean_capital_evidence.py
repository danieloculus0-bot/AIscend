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

from .position_cost_basis import coinbase_fill_time_key


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
    try:
        # Keep original exchange decimal strings intact. A float round-trip
        # can lose precision on crypto quantities and falsify fee-net P/L.
        size = Decimal(str(raw["size"]))
        price = Decimal(str(raw["price"]))
        fee = Decimal(str(raw["commission"]))
        if not all(x.is_finite() for x in (size, price, fee)):
            raise ValueError("Nonfinite Coinbase fill")
        if size <= 0 or price <= 0:
            raise ValueError("Invalid Coinbase fill size or price")
        # Coinbase maker rebates may legitimately be negative commission.
    except (KeyError, TypeError, ValueError, InvalidOperation, ArithmeticError):
        raise ValueError("Coinbase fill size, price or commission is incomplete")
    # Preserve legitimate signed maker rebates, but never allow an
    # impossible commission to manufacture a large closed-trade profit.
    if abs(fee) > size * price:
        raise ValueError("Coinbase fill commission exceeds trade notional")
    stamp = str(raw.get("trade_time") or "").strip()
    if not stamp:
        raise ValueError("Coinbase fill timestamp missing")
    coinbase_fill_time_key({"trade_time": stamp})
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
        "size": str(size), "price": str(price),
        "fee": str(fee), "fee_currency": fee_currency,
        "trade_time": stamp,
        "order_id": str(raw.get("order_id") or ""),
        "source": "authenticated_coinbase_advanced_get_fills",
    }


def _valid_scope(scope):
    return (isinstance(scope, str) and len(scope) == 64 and
            all(c in "0123456789abcdef" for c in scope))


def _require_scope(scope):
    if not _valid_scope(scope):
        raise ValueError("Verified Coinbase credential scope required")
    return scope


def _scope_meta(key, scope):
    return key + ":" + _require_scope(scope)


def _fill_table(store):
    # Never import legacy, unscoped fills: their account is unknown.
    store.conn.execute("""CREATE TABLE IF NOT EXISTS bean_account_fills (
        account_scope TEXT NOT NULL, fill_id TEXT NOT NULL,
        evidence TEXT NOT NULL, observed_at REAL NOT NULL,
        PRIMARY KEY (account_scope, fill_id))""")


def preserve_fills(store, response, *, account_scope, clock=time.time):
    """Idempotently retain all observed fills, including an incomplete page run."""
    if not isinstance(response, dict) or not isinstance(response.get("fills"), list):
        raise ValueError("Coinbase fills response missing list")
    scope = _require_scope(account_scope)
    _fill_table(store)
    normalized = [_normalize_fill(item) for item in response["fills"]]
    # A single authenticated pagination result must not repeat a fill ID.
    # Idempotent re-reads across separate snapshots remain valid, but repeated
    # IDs within one response can hide overlapping or malformed pages.
    ids = [item["fill_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("Coinbase authenticated fill history repeats a fill ID")
    if response.get("complete") is True:
        # A fully paginated account snapshot must include every previously
        # observed ID. Otherwise credentials, portfolio scope, or retention
        # changed and historical results cannot be reconciled safely.
        previous = {
            row["fill_id"] for row in store.conn.execute(
                "SELECT fill_id FROM bean_account_fills WHERE account_scope=?",
                (scope,))
        }
        incoming = {row["fill_id"] for row in normalized}
        if previous - incoming:
            store.set_meta(_scope_meta("bean_fills_last_complete_at", scope), "")
            raise ValueError("Coinbase complete history excludes prior recorded fills")
    with store.conn:
        for fill in normalized:
            payload = json.dumps(fill, sort_keys=True)
            previous = store.conn.execute(
                "SELECT evidence FROM bean_account_fills WHERE account_scope=? AND fill_id=?",
                (scope, fill["fill_id"])).fetchone()
            if previous is not None and previous["evidence"] != payload:
                raise ValueError("Coinbase returned conflicting evidence for a fill ID")
            if previous is None:
                store.conn.execute(
                    "INSERT INTO bean_account_fills(account_scope,fill_id,evidence,observed_at) "
                    "VALUES(?,?,?,?)", (scope, fill["fill_id"], payload, float(clock())))
    if response.get("complete") is True:
        store.set_meta(_scope_meta("bean_fills_last_complete_at", scope),
                       str(float(clock())))
    else:
        store.set_meta(_scope_meta("bean_fills_last_complete_at", scope), "")
    store.set_meta(_scope_meta("bean_fills_last_attempt_at", scope),
                   str(float(clock())))
    return len(normalized)


def execution_history(store, *, account_scope, fresh_seconds=900, clock=time.time):
    """Never declare a partial or stale exchange history complete."""
    scope = _require_scope(account_scope)
    _fill_table(store)
    last = _money(store.get_meta(_scope_meta("bean_fills_last_complete_at", scope)))
    fresh = last is not None and 0 <= float(clock()) - last <= fresh_seconds
    rows = [json.loads(row["evidence"]) for row in
            store.conn.execute(
                "SELECT evidence FROM bean_account_fills WHERE account_scope=?",
                (scope,)).fetchall()]
    try:
        rows.sort(key=lambda x: (coinbase_fill_time_key(x), x["fill_id"]))
    except (ValueError, TypeError, OverflowError):
        return {
            "status": "UNVERIFIED_OR_STALE",
            "last_full_provider_read_at": last,
            "fill_count_retained": len(rows),
            "history_fully_reconciled": False,
            "products": {}, "recent_trades": [],
            "note": "Stored Coinbase fill timestamp is invalid; closed P/L cannot be verified.",
        }
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
    sides_by_instant = {}
    for row in rows:
        p = row["product"]
        instant = coinbase_fill_time_key(row)
        previous_side = sides_by_instant.setdefault((p, instant), row["side"])
        if previous_side != row["side"]:
            invalid.add(p)
            continue
        q = p.rsplit("-", 1)[1]
        # Missing or differently denominated fees invalidate *net* evidence
        # on that product; other products may still have reconciled histories.
        if row["fee_currency"] != q:
            invalid.add(p)
            continue
        size, price, fee = (Decimal(row[key]) for key in ("size", "price", "fee"))
        units, cost = balances[p]
        if row["side"] == "BUY":
            lot_cost = size * price + fee
            if lot_cost <= 0:
                invalid.add(p)
                continue
            balances[p] = [units + size, cost + lot_cost]
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
    """Real account evidence, strictly isolated by active verified key scope."""
    capital = capital_snapshot(snapshot)
    scope_getter = getattr(rail, "account_scope_id", None)
    try:
        scope = scope_getter() if callable(scope_getter) else None
    except Exception:
        scope = None
    if not _valid_scope(scope) or snapshot.get("authenticated_scope") != scope:
        # Missing or switched credentials cannot inherit any previous account's
        # funds, entry baseline, closed trades or prior complete-history flag.
        return {
            "capital": {**capital, "starting_equity_usd": None,
                        "equity_change_usd": None,
                        "baseline_description": "unverified_account_scope"},
            "execution": {"status": "UNVERIFIED_ACCOUNT_SCOPE",
                          "fill_count_retained": 0, "history_fully_reconciled": False,
                          "products": {}, "recent_trades": []},
            "read_status": "AUTHENTICATED_ACCOUNT_SCOPE_UNAVAILABLE",
            "account_scope_verified": False,
            "realized_account_profit_verified": False,
        }
    last = _money(store.get_meta(_scope_meta("bean_fills_last_attempt_at", scope)))
    now = float(clock())
    reader = getattr(rail, "account_fills", None)
    status = "NOT_QUERIED"
    if not callable(reader):
        status = "AUTHENTICATED_FILL_READER_UNAVAILABLE"
    elif last is None or now - last >= refresh_seconds or now < last:
        store.set_meta(_scope_meta("bean_fills_last_attempt_at", scope), str(now))
        try:
            response = reader()
            # The fill API may use a newly selected SDK credential. Never
            # persist another account's fills under the original scope when
            # the key changes during pagination or the authenticated read.
            if scope_getter() != scope:
                raise RuntimeError(
                    "Coinbase credential scope changed during fill history read")
            preserve_fills(store, response, account_scope=scope, clock=clock)
            status = ("PROVIDER_HISTORY_COMPLETE" if response.get("complete") is True
                      else "PROVIDER_HISTORY_PARTIAL")
        except Exception as exc:
            store.set_meta(_scope_meta("bean_fills_last_complete_at", scope), "")
            status = "HISTORY_READ_FAILED:" + type(exc).__name__
    else:
        status = "CACHED_HISTORY"
    return {
        "capital": capital,
        "execution": execution_history(store, account_scope=scope, clock=clock),
        "read_status": status,
        "account_scope_verified": True,
        "realized_account_profit_verified": False,
    }
