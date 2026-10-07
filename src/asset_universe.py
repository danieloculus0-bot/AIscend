from __future__ import annotations

import json
import math
import statistics
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


COINBASE_PUBLIC = "https://api.exchange.coinbase.com"


@dataclass(frozen=True)
class UniverseAsset:
    product: str
    base: str
    quote: str
    price: float
    return_5m: float
    return_1h: float
    return_6h: float
    volume_ratio: float
    spread_bps: float
    score: float
    scanned_at: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CoinbaseAssetUniverse:
    """
    Rotating public-market scanner across active Coinbase USD/USDC products.

    It discovers the full active product universe, then scores a rotating batch on
    each cycle so AIscend can cover many assets without hammering the public API.
    This is research breadth only; execution readiness is handled separately.
    """

    _lock = threading.Lock()
    _products: list[dict[str, Any]] = []
    _products_until = 0.0
    _cursor = 0
    _cache: dict[str, UniverseAsset] = {}

    def __init__(
        self,
        timeout: float = 6.0,
        batch_size: int = 8,
        product_refresh_seconds: float = 900.0,
        asset_cache_seconds: float = 600.0,
    ) -> None:
        self.timeout = timeout
        self.batch_size = max(1, int(batch_size))
        self.product_refresh_seconds = max(60.0, float(product_refresh_seconds))
        self.asset_cache_seconds = max(60.0, float(asset_cache_seconds))

    def collect(self) -> dict[str, Any]:
        errors: list[str] = []
        with self._lock:
            products = self._product_list(errors)
            if not products:
                return {
                    "available": False,
                    "product_count": 0,
                    "scanned_count": len(self._cache),
                    "top": [],
                    "errors": errors,
                }

            batch = self._next_batch(products)

        for product in batch:
            product_id = str(product.get("id") or "")
            if not product_id:
                continue
            cached = self._cache.get(product_id)
            if cached and time.time() - cached.scanned_at < self.asset_cache_seconds:
                continue
            try:
                asset = self._scan_product(product)
                if asset is not None:
                    with self._lock:
                        self._cache[product_id] = asset
            except Exception as exc:
                errors.append(f"{product_id}: {exc}")

        with self._lock:
            fresh = [
                item
                for item in self._cache.values()
                if time.time() - item.scanned_at <= self.asset_cache_seconds * 3
            ]

        ranked = sorted(
            fresh,
            key=lambda item: (
                abs(item.score),
                abs(item.return_1h),
                item.volume_ratio,
            ),
            reverse=True,
        )

        return {
            "available": bool(ranked),
            "product_count": len(products),
            "scanned_count": len(fresh),
            "top": [item.as_dict() for item in ranked[:20]],
            "errors": errors,
        }

    def _product_list(self, errors: list[str]) -> list[dict[str, Any]]:
        now = time.time()
        cls = type(self)
        if cls._products and now < cls._products_until:
            return cls._products

        try:
            payload = self._get_json("/products")
            if not isinstance(payload, list):
                raise RuntimeError("unexpected products payload")
            rows: list[dict[str, Any]] = []
            for item in payload:
                status = str(item.get("status") or "").lower()
                if status and status != "online":
                    continue
                if item.get("trading_disabled") is True:
                    continue
                quote = str(item.get("quote_currency") or "").upper()
                base = str(item.get("base_currency") or "").upper()
                product_id = str(item.get("id") or "").upper()
                if quote not in {"USD", "USDC"}:
                    continue
                if not base or not product_id:
                    continue
                rows.append(
                    {
                        "id": product_id,
                        "base_currency": base,
                        "quote_currency": quote,
                    }
                )
            rows.sort(key=lambda item: item["id"])
            cls._products = rows
            cls._products_until = now + self.product_refresh_seconds
            cls._cursor %= max(1, len(rows))
            return rows
        except Exception as exc:
            errors.append(f"coinbase_products: {exc}")
            return cls._products

    def _next_batch(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cls = type(self)
        if not products:
            return []
        start = cls._cursor % len(products)
        out = [products[(start + i) % len(products)] for i in range(min(self.batch_size, len(products)))]
        cls._cursor = (start + len(out)) % len(products)
        return out

    def _scan_product(self, product: dict[str, Any]) -> UniverseAsset | None:
        product_id = str(product["id"])
        candles = self._get_json(
            f"/products/{urllib.parse.quote(product_id)}/candles?granularity=300"
        )
        ticker = self._get_json(f"/products/{urllib.parse.quote(product_id)}/ticker")
        book = self._get_json(f"/products/{urllib.parse.quote(product_id)}/book?level=1")

        if not isinstance(candles, list) or len(candles) < 72:
            return None

        ordered = list(reversed(candles))
        closes = [float(row[4]) for row in ordered]
        volumes = [float(row[5]) for row in ordered]
        price = float(ticker["price"])
        return_5m = self._pct(closes[-1], closes[-2])
        return_1h = self._pct(closes[-1], closes[-13])
        return_6h = self._pct(closes[-1], closes[-72])

        baseline = statistics.median(volumes[-13:-1]) or 1.0
        volume_ratio = volumes[-1] / baseline

        bid = float(book["bids"][0][0])
        ask = float(book["asks"][0][0])
        mid = (bid + ask) / 2.0
        spread_bps = ((ask - bid) / mid) * 10000.0 if mid else 0.0

        def squash(value: float, scale: float) -> float:
            return math.tanh(value / scale) if scale else 0.0

        score = (
            0.20 * squash(return_5m, 0.005)
            + 0.38 * squash(return_1h, 0.015)
            + 0.30 * squash(return_6h, 0.04)
            + 0.12 * squash(volume_ratio - 1.0, 0.8)
        )
        spread_penalty = min(0.20, spread_bps / 150.0)
        score *= max(0.25, 1.0 - spread_penalty)

        return UniverseAsset(
            product=product_id,
            base=str(product["base_currency"]),
            quote=str(product["quote_currency"]),
            price=price,
            return_5m=return_5m,
            return_1h=return_1h,
            return_6h=return_6h,
            volume_ratio=volume_ratio,
            spread_bps=spread_bps,
            score=max(-1.0, min(1.0, score)),
            scanned_at=time.time(),
        )

    def _get_json(self, path: str) -> Any:
        request = urllib.request.Request(
            COINBASE_PUBLIC + path,
            headers={
                "Accept": "application/json",
                "User-Agent": "AIscend/0.11 asset-universe",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _pct(new: float, old: float) -> float:
        return (new / old) - 1.0 if old else 0.0
