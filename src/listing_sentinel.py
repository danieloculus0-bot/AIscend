from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .core import StateStore


COINBASE_PUBLIC = "https://api.exchange.coinbase.com"


@dataclass(frozen=True)
class ListingEvent:
    ts: float
    product: str
    base: str
    quote: str
    event: str
    stage: str
    status: str
    trading_disabled: bool
    cancel_only: bool
    limit_only: bool
    post_only: bool
    auction_mode: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CoinbaseListingSentinel:
    """Detect public Coinbase spot-product additions and trading-state changes.

    This watches only public exchange metadata. It does not use leaked,
    privileged, or non-public listing information.
    """

    SNAPSHOT_META = "listing_sentinel_snapshot_v1"
    EVENTS_META = "listing_sentinel_events_v1"

    def __init__(
        self,
        store: StateStore,
        *,
        timeout: float = 6.0,
        poll_seconds: float = 55.0,
        fetch_json: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store
        self.timeout = float(timeout)
        self.poll_seconds = max(10.0, float(poll_seconds))
        self.fetch_json = fetch_json or self._get_json
        self._last_result: dict[str, Any] | None = None
        self._next_poll_at = 0.0

    @staticmethod
    def _stage(item: dict[str, Any]) -> str:
        status = str(item.get("status") or "").upper()
        if bool(item.get("auction_mode", False)):
            return "AUCTION"
        if bool(item.get("cancel_only", False)):
            return "CANCEL_ONLY"
        if bool(item.get("limit_only", False)):
            return "LIMIT_ONLY"
        if bool(item.get("post_only", False)):
            return "POST_ONLY"
        if bool(item.get("trading_disabled", False)):
            return "DISCOVERED"
        if status in {"ONLINE", "ACTIVE", ""}:
            return "FULL_TRADING"
        return status or "DISCOVERED"

    @classmethod
    def _normalize(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        product = str(item.get("id") or item.get("product_id") or "").upper()
        base = str(item.get("base_currency") or item.get("base_currency_id") or "").upper()
        quote = str(item.get("quote_currency") or item.get("quote_currency_id") or "").upper()
        if not product or not base or not quote:
            return None
        row = {
            "product": product,
            "base": base,
            "quote": quote,
            "status": str(item.get("status") or "").upper(),
            "trading_disabled": bool(item.get("trading_disabled", False)),
            "cancel_only": bool(item.get("cancel_only", False)),
            "limit_only": bool(item.get("limit_only", False)),
            "post_only": bool(item.get("post_only", False)),
            "auction_mode": bool(item.get("auction_mode", False)),
        }
        row["stage"] = cls._stage(row)
        return row

    def poll(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._last_result is not None and now < self._next_poll_at:
            return self._last_result

        try:
            payload = self.fetch_json("/products")
            if not isinstance(payload, list):
                raise RuntimeError("unexpected Coinbase products payload")
            current_rows = {}
            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                row = self._normalize(raw)
                if row is not None:
                    current_rows[row["product"]] = row
        except Exception as exc:
            result = {
                "available": False,
                "product_count": 0,
                "new_products": [],
                "changes": [],
                "hot": self._recent_events(now),
                "errors": [str(exc)],
                "scanned_at": now,
            }
            self._last_result = result
            self._next_poll_at = now + self.poll_seconds
            return result

        previous = self._load_snapshot()
        new_events: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []

        if previous:
            for product, row in current_rows.items():
                before = previous.get(product)
                if before is None:
                    event = self._event(now, row, "NEW_PRODUCT")
                    new_events.append(event)
                    changes.append(event)
                    continue

                watched = (
                    "stage",
                    "status",
                    "trading_disabled",
                    "cancel_only",
                    "limit_only",
                    "post_only",
                    "auction_mode",
                )
                if any(before.get(key) != row.get(key) for key in watched):
                    event_name = "TRADING_STATE_CHANGE"
                    if bool(before.get("trading_disabled", False)) and not row["trading_disabled"]:
                        event_name = "TRADING_ENABLED"
                    if row["auction_mode"] and not bool(before.get("auction_mode", False)):
                        event_name = "AUCTION_STARTED"
                    event = self._event(now, row, event_name)
                    changes.append(event)

        self.store.set_meta(
            self.SNAPSHOT_META,
            json.dumps(current_rows, separators=(",", ":"), sort_keys=True),
        )

        if changes:
            history = self._load_events()
            history.extend(changes)
            history = history[-500:]
            self.store.set_meta(
                self.EVENTS_META,
                json.dumps(history, separators=(",", ":")),
            )

        result = {
            "available": True,
            "baseline_created": not bool(previous),
            "product_count": len(current_rows),
            "new_products": new_events,
            "changes": changes,
            "hot": self._recent_events(now),
            "errors": [],
            "scanned_at": now,
        }
        self._last_result = result
        self._next_poll_at = now + self.poll_seconds
        return result

    @staticmethod
    def _event(ts: float, row: dict[str, Any], event: str) -> dict[str, Any]:
        return ListingEvent(
            ts=ts,
            product=row["product"],
            base=row["base"],
            quote=row["quote"],
            event=event,
            stage=row["stage"],
            status=row["status"],
            trading_disabled=row["trading_disabled"],
            cancel_only=row["cancel_only"],
            limit_only=row["limit_only"],
            post_only=row["post_only"],
            auction_mode=row["auction_mode"],
        ).as_dict()

    def _load_snapshot(self) -> dict[str, dict[str, Any]]:
        raw = self.store.get_meta(self.SNAPSHOT_META)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _load_events(self) -> list[dict[str, Any]]:
        raw = self.store.get_meta(self.EVENTS_META)
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    def _recent_events(self, now: float, max_age_seconds: float = 86400.0) -> list[dict[str, Any]]:
        return [
            event
            for event in self._load_events()
            if now - float(event.get("ts") or 0.0) <= max_age_seconds
        ][-50:][::-1]

    def _get_json(self, path: str) -> Any:
        request = urllib.request.Request(
            COINBASE_PUBLIC + path,
            headers={
                "Accept": "application/json",
                "User-Agent": "AIscend/0.16 listing-sentinel",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
