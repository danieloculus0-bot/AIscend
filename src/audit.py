from __future__ import annotations

import csv
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .core import app_data_dir


AUDIT_SCHEMA_VERSION = 1
AUDIT_FIELDS = (
    "schema_version",
    "event_id",
    "timestamp_utc",
    "event_type",
    "venue",
    "rail",
    "network",
    "side",
    "product_id",
    "asset",
    "base_asset",
    "quote_asset",
    "base_quantity",
    "quote_quantity",
    "unit_price_quote",
    "fee_asset",
    "fee_quantity",
    "order_id",
    "client_order_id",
    "transaction_id",
    "status",
    "rationale",
    "source",
    "from_location",
    "to_location",
    "raw_provider_response",
)

_SENSITIVE_KEY_PARTS = (
    "secret",
    "private",
    "password",
    "credential",
    "api_key",
    "apikey",
    "token",
)
_WRITE_LOCK = threading.Lock()


def _decimal_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                clean[key_text] = "[REDACTED]"
            else:
                clean[key_text] = _sanitize(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _find_first(payload: Any, keys: Iterable[str]) -> str:
    wanted = {key.lower() for key in keys}

    def walk(value: Any) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in wanted and item not in (None, ""):
                    return str(item)
            for item in value.values():
                found = walk(item)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return ""

    return walk(payload)


class AuditTrail:
    """Append-only local audit trail for trades and wallet movements.

    The JSONL file is the canonical machine-readable record. A matching CSV is
    maintained for quick tax-season review. Files live outside the repository
    under AIscend's application-data directory.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (app_data_dir() / "audit")
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def provider_id(payload: Any, *keys: str) -> str:
        return _find_first(payload, keys)

    def _paths(self, timestamp: datetime) -> tuple[Path, Path]:
        year = timestamp.astimezone(timezone.utc).year
        return (
            self.root / f"aiscend-audit-{year}.jsonl",
            self.root / f"aiscend-audit-{year}.csv",
        )

    def record_event(
        self,
        *,
        event_type: str,
        venue: str,
        rail: str,
        network: str,
        side: str = "",
        product_id: str = "",
        asset: str = "",
        base_asset: str = "",
        quote_asset: str = "",
        base_quantity: Any = None,
        quote_quantity: Any = None,
        unit_price_quote: Any = None,
        fee_asset: str = "",
        fee_quantity: Any = None,
        order_id: str = "",
        client_order_id: str = "",
        transaction_id: str = "",
        status: str = "",
        rationale: str = "",
        source: str = "",
        from_location: str = "",
        to_location: str = "",
        raw_provider_response: Any = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        stamp = timestamp or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)

        raw = _sanitize(raw_provider_response)
        row: dict[str, Any] = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "event_id": str(uuid.uuid4()),
            "timestamp_utc": stamp.isoformat().replace("+00:00", "Z"),
            "event_type": str(event_type),
            "venue": str(venue),
            "rail": str(rail),
            "network": str(network),
            "side": str(side),
            "product_id": str(product_id),
            "asset": str(asset),
            "base_asset": str(base_asset),
            "quote_asset": str(quote_asset),
            "base_quantity": _decimal_text(base_quantity),
            "quote_quantity": _decimal_text(quote_quantity),
            "unit_price_quote": _decimal_text(unit_price_quote),
            "fee_asset": str(fee_asset),
            "fee_quantity": _decimal_text(fee_quantity),
            "order_id": str(order_id),
            "client_order_id": str(client_order_id),
            "transaction_id": str(transaction_id),
            "status": str(status),
            "rationale": str(rationale),
            "source": str(source),
            "from_location": str(from_location),
            "to_location": str(to_location),
            "raw_provider_response": raw,
        }

        jsonl_path, csv_path = self._paths(stamp)
        csv_row = dict(row)
        csv_row["raw_provider_response"] = json.dumps(
            raw, separators=(",", ":"), ensure_ascii=False
        )

        with _WRITE_LOCK:
            with jsonl_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            write_header = not csv_path.exists() or csv_path.stat().st_size == 0
            with csv_path.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerow(csv_row)
                handle.flush()
                os.fsync(handle.fileno())

        return row

    def record_trade(
        self,
        *,
        venue: str,
        rail: str,
        network: str,
        side: str,
        product_id: str,
        base_asset: str,
        quote_asset: str,
        base_quantity: Any = None,
        quote_quantity: Any = None,
        unit_price_quote: Any = None,
        fee_asset: str = "",
        fee_quantity: Any = None,
        order_id: str = "",
        client_order_id: str = "",
        transaction_id: str = "",
        status: str = "",
        rationale: str = "",
        source: str = "",
        raw_provider_response: Any = None,
    ) -> dict[str, Any]:
        return self.record_event(
            event_type="trade",
            venue=venue,
            rail=rail,
            network=network,
            side=side.upper(),
            product_id=product_id.upper(),
            base_asset=base_asset.upper(),
            quote_asset=quote_asset.upper(),
            base_quantity=base_quantity,
            quote_quantity=quote_quantity,
            unit_price_quote=unit_price_quote,
            fee_asset=fee_asset.upper(),
            fee_quantity=fee_quantity,
            order_id=order_id,
            client_order_id=client_order_id,
            transaction_id=transaction_id,
            status=status,
            rationale=rationale,
            source=source,
            raw_provider_response=raw_provider_response,
        )

    def record_transfer(
        self,
        *,
        venue: str,
        rail: str,
        network: str,
        asset: str,
        quantity: Any,
        direction: str,
        from_location: str,
        to_location: str,
        transaction_id: str = "",
        status: str = "",
        source: str = "",
        raw_provider_response: Any = None,
    ) -> dict[str, Any]:
        return self.record_event(
            event_type="transfer",
            venue=venue,
            rail=rail,
            network=network,
            side=direction.upper(),
            asset=asset.upper(),
            base_quantity=quantity,
            transaction_id=transaction_id,
            status=status,
            source=source,
            from_location=from_location,
            to_location=to_location,
            raw_provider_response=raw_provider_response,
        )
