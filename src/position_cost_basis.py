"""Reconstruct a held spot asset's cost from authenticated Coinbase fills.

This executes no trades. The result is only labelled verified when its final
quantity matches the authenticated asset balance and the fill history is complete.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import re


_RFC3339 = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def coinbase_fill_time_key(row: dict) -> tuple[int, int]:
    """Sort Coinbase RFC3339 fills by actual UTC instant, retaining nanoseconds.

    Plain text sorting reverses whole-second 'Z' and fractional '.xxxZ'
    timestamps, potentially turning an unmatched SELL into fake closed profit.
    """
    stamp = str(row.get("trade_time") or "").strip()
    match = _RFC3339.fullmatch(stamp)
    if match is None:
        raise ValueError("Invalid Coinbase fill timestamp")
    whole, fraction, offset = match.groups()
    instant = datetime.fromisoformat(whole + ("+00:00" if offset == "Z" else offset))
    seconds = int(instant.astimezone(timezone.utc).timestamp())
    nanos = int((fraction or "0").ljust(9, "0"))
    return seconds, nanos


def rebuild_position_basis(product: str, held_quantity, fills: list[dict],
                           *, complete_history: bool) -> dict:
    def unknown(reason: str) -> dict:
        return {"cost_basis_status": reason, "avg_cost": None,
                "fill_count": len(fills), "product": product}

    if not complete_history:
        return unknown("incomplete_coinbase_fill_history")
    try:
        target = Decimal(str(held_quantity))
        if not target.is_finite() or target < 0:
            return unknown("invalid_held_quantity")
        rows = sorted(fills, key=lambda row: (coinbase_fill_time_key(row),
                                             str(row.get("entry_id") or row.get("trade_id") or "")))
        qty = Decimal("0")
        cost = Decimal("0")
        seen = set()
        sides_by_instant = {}
        for row in rows:
            if str(row.get("product_id") or "").upper() != product.upper():
                return unknown("mismatched_coinbase_fill_product")
            fill_id = str(row.get("entry_id") or row.get("trade_id") or "")
            if not fill_id or fill_id in seen:
                return unknown("missing_or_duplicate_fill_id")
            seen.add(fill_id)
            size = Decimal(str(row["size"]))
            price = Decimal(str(row["price"]))
            if row.get("commission") in (None, ""):
                return unknown("missing_coinbase_fill_commission")
            fee = Decimal(str(row["commission"]))
            if not all(v.is_finite() for v in (size, price, fee)):
                return unknown("nonfinite_coinbase_fill")
            # Authenticated maker rebates can be negative commissions.
            # Reject impossible lots, not the sign of a valid signed fee.
            if size <= 0 or price <= 0:
                return unknown("invalid_coinbase_fill")
            quote = product.rpartition("-")[2].upper()
            fee_currency = str(
                row.get("commission_currency") or row.get("fee_currency") or quote
            ).upper()
            if fee_currency != quote:
                return unknown("mismatched_coinbase_fee_currency")
            side = str(row.get("side") or "").upper()
            instant = coinbase_fill_time_key(row)
            earlier_side = sides_by_instant.setdefault(instant, side)
            if earlier_side != side:
                return unknown("ambiguous_same_instant_coinbase_fills")
            if side == "BUY":
                lot_cost = size * price + fee
                if lot_cost <= 0:
                    return unknown("nonpositive_coinbase_lot_cost")
                qty += size
                cost += lot_cost
            elif side == "SELL":
                if qty < size:
                    return unknown("sell_exceeds_recorded_buy_lots")
                per_unit = cost / qty if qty else Decimal("0")
                cost -= per_unit * size
                qty -= size
            else:
                return unknown("unknown_coinbase_fill_side")
    except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError):
        return unknown("invalid_coinbase_fill_payload")
    tolerance = max(Decimal("0.00000001"), target * Decimal("0.0001"))
    if abs(qty - target) > tolerance:
        return unknown("coinbase_fill_balance_mismatch_or_transfer")
    if qty <= 0 or cost <= 0:
        return unknown("no_open_coinbase_lot")
    return {"cost_basis_status": "verified_coinbase_fills",
            "avg_cost": float(cost / qty), "quantity": float(qty),
            "total_quote_cost": float(cost), "fill_count": len(fills),
            "product": product}
