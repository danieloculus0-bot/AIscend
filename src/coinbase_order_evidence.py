"""Coinbase order evidence: never confuse accepted submissions with filled trades.

This module is intentionally free of Coinbase credentials and mutable state.
It interprets only an authenticated order-detail response supplied by the rail.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def accepted_order_id(response: dict[str, Any] | None) -> str | None:
    if not isinstance(response, dict):
        return None
    for source in (response.get("success_response"), response):
        if isinstance(source, dict):
            value = source.get("order_id") or source.get("orderId")
            if value:
                return str(value)
    return None


def verified_order(detail: dict[str, Any] | None, order_id: str) -> dict[str, Any]:
    """An accepted order becomes FILLED_VERIFIED only with explicit venue evidence."""
    detail = detail or {}
    order = detail.get("order") if isinstance(detail.get("order"), dict) else detail
    if not isinstance(order, dict):
        order = {}
    status = str(order.get("status") or order.get("order_status") or "UNKNOWN").upper()
    venue_id = str(order.get("order_id") or order.get("orderId") or "")
    if venue_id and venue_id != order_id:
        return {"state": "UNVERIFIED", "reason": "Order detail ID mismatch",
                "venue_status": status}
    raw_qty = order.get("filled_size")
    if raw_qty is None:
        raw_qty = order.get("filled_quantity")
    # A missing or malformed quantity does NOT mean zero executed quantity.
    # A terminal order can have partial fills; clearing its durable client ID
    # on ambiguous venue evidence risks submitting an unintended second order.
    try:
        quantity = Decimal(str(raw_qty)) if raw_qty is not None else None
    except (InvalidOperation, ValueError, TypeError):
        quantity = None
    known_quantity = (
        quantity is not None and quantity.is_finite() and quantity >= 0)
    raw_value = order.get("filled_value")
    try:
        filled_value = (
            Decimal(str(raw_value)) if raw_value is not None else None)
    except (InvalidOperation, ValueError, TypeError):
        filled_value = None
    value_consistent = (
        raw_value is None or
        (filled_value is not None and filled_value.is_finite() and
         filled_value >= 0 and
         (quantity is None or quantity != 0 or filled_value == 0))
    )
    if status == "FILLED" and known_quantity and quantity > 0:
        return {"state": "FILLED_VERIFIED", "order_id": order_id,
                "venue_status": status, "filled_size": str(quantity),
                "filled_value": order.get("filled_value"),
                "average_filled_price": order.get("average_filled_price"),
                "total_fees": order.get("total_fees"),
                "evidence": "coinbase_authenticated_order_detail"}
    if status in {"FAILED", "REJECTED", "CANCELLED", "EXPIRED"}:
        if not known_quantity or not value_consistent:
            return {"state": "TERMINAL_FILL_UNVERIFIED", "order_id": order_id,
                    "venue_status": status,
                    "reason": "Terminal Coinbase order lacks consistent executed-quantity evidence; "
                              "do not release pending intent until fills are reconciled"}
        if quantity > 0:
            return {"state": "PARTIAL_FILL_REQUIRES_RECONCILIATION",
                    "order_id": order_id, "venue_status": status,
                    "filled_size": str(quantity),
                    "reason": "Partially filled terminal order; exchange fills and balances must be reconciled"}
        return {"state": "VENUE_NOT_FILLED", "order_id": order_id,
                "venue_status": status,
                "reason": "Authenticated venue explicitly confirmed zero filled quantity"}
    return {"state": "ORDER_ACCEPTED_UNVERIFIED", "order_id": order_id,
            "venue_status": status,
            "reason": "Accepted submission but no full Coinbase fill confirmation"}
