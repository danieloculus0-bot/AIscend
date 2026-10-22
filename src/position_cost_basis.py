"""Reconstruct a held spot asset's cost from authenticated Coinbase fills.

This executes no trades. The result is only labelled verified when its final
quantity matches the authenticated asset balance and the fill history is complete.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


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
        rows = sorted(fills, key=lambda row: str(row.get("trade_time") or ""))
        qty = Decimal("0")
        cost = Decimal("0")
        seen = set()
        for row in rows:
            if str(row.get("product_id") or "").upper() != product.upper():
                return unknown("mismatched_coinbase_fill_product")
            fill_id = str(row.get("entry_id") or row.get("trade_id") or "")
            if not fill_id or fill_id in seen:
                return unknown("missing_or_duplicate_fill_id")
            seen.add(fill_id)
            size = Decimal(str(row["size"]))
            price = Decimal(str(row["price"]))
            fee = Decimal(str(row.get("commission") or "0"))
            if not all(v.is_finite() for v in (size, price, fee)):
                return unknown("nonfinite_coinbase_fill")
            if size <= 0 or price <= 0 or fee < 0:
                return unknown("invalid_coinbase_fill")
            side = str(row.get("side") or "").upper()
            if side == "BUY":
                qty += size
                cost += size * price + fee
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
