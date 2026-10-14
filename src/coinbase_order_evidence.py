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
    try:
        quantity = Decimal(str(raw_qty)) if raw_qty is not None else Decimal(0)
    except (InvalidOperation, ValueError):
        quantity = Decimal(0)
    if status == "FILLED" and quantity.is_finite() and quantity > 0:
        return {"state": "FILLED_VERIFIED", "order_id": order_id,
                "venue_status": status, "filled_size": str(quantity),
                "filled_value": order.get("filled_value"),
                "average_filled_price": order.get("average_filled_price"),
                "total_fees": order.get("total_fees"),
                "evidence": "coinbase_authenticated_order_detail"}
    if status in {"FAILED", "REJECTED", "CANCELLED", "EXPIRED"}:
        return {"state": "VENUE_NOT_FILLED", "order_id": order_id,
                "venue_status": status,
                "reason": "Authenticated venue did not confirm a full fill"}
    return {"state": "ORDER_ACCEPTED_UNVERIFIED", "order_id": order_id,
            "venue_status": status,
            "reason": "Accepted submission but no full Coinbase fill confirmation"}
