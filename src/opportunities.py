from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .asset_universe import CoinbaseAssetUniverse
from .research import MarketResearch
from .venues import capability_snapshot
from .wallets.advanced_trade import AdvancedTradeSpot, AdvancedTradeVault


KALSHI_PUBLIC = "https://external-api.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class Opportunity:
    venue: str
    instrument: str
    title: str
    score: float
    confidence: float
    execution_ready: bool
    details: dict[str, Any]
    priority: float = 0.0
    score_kind: str = "directional_edge"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpportunityUniverse:
    """Normalize Coinbase-native research lanes into one ranked opportunity view.

    Crypto currently has live execution. Prediction markets are fully discovered
    and ranked for research, but autonomous Coinbase prediction execution is not
    claimed until Coinbase exposes a supported programmatic execution path for
    the connected CFM account.
    """

    SPORTS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("NFL", ("NFL", "SUPER BOWL", "FOOTBALL")),
        ("NBA", ("NBA", "BASKETBALL")),
        ("WNBA", ("WNBA",)),
        ("MLB", ("MLB", "BASEBALL", "WORLD SERIES")),
        ("NHL", ("NHL", "HOCKEY", "STANLEY CUP")),
        ("NCAAF", ("NCAAF", "COLLEGE FOOTBALL", "CFB")),
        ("NCAAB", ("NCAAB", "COLLEGE BASKETBALL", "MARCH MADNESS")),
        ("SOCCER", ("SOCCER", "PREMIER LEAGUE", "CHAMPIONS LEAGUE", "MLS", "FIFA")),
        ("TENNIS", ("TENNIS", "ATP", "WTA", "WIMBLEDON", "US OPEN")),
        ("MMA", ("UFC", "MMA")),
        ("GOLF", ("PGA", "GOLF", "MASTERS")),
        ("MOTORSPORT", ("F1", "FORMULA 1", "NASCAR", "INDYCAR")),
        ("ESPORTS", ("ESPORTS", "LEAGUE OF LEGENDS", "VALORANT", "COUNTER-STRIKE")),
    )

    def __init__(self, timeout: float = 8.0, max_prediction_pages: int = 8) -> None:
        self.timeout = timeout
        self.max_prediction_pages = max(1, int(max_prediction_pages))
        self._prediction_scan_meta: dict[str, Any] = {}

    def collect(self) -> dict[str, Any]:
        errors: list[str] = []
        opportunities: list[Opportunity] = []

        crypto = MarketResearch(timeout=self.timeout).collect()
        if crypto.available and crypto.eth is not None:
            edge = float(crypto.composite_score)
            confidence = float(crypto.confidence)
            opportunities.append(
                Opportunity(
                    venue="crypto",
                    instrument="ETH/USDC",
                    title="Ethereum spot via WETH/USDC on Base",
                    score=edge,
                    confidence=confidence,
                    execution_ready=True,
                    details={
                        "prediction": crypto.prediction,
                        "thesis": crypto.thesis,
                        "price": crypto.eth.price,
                    },
                    priority=self._edge_priority(edge, confidence),
                    score_kind="directional_edge",
                )
            )
        else:
            errors.extend(crypto.errors)

        try:
            tradable_products: set[str] = set()
            if AdvancedTradeVault().configured():
                try:
                    tradable_products = set(AdvancedTradeSpot().status().product_ids)
                except Exception as exc:
                    errors.append(f"advanced_account_status: {exc}")

            universe = CoinbaseAssetUniverse(timeout=self.timeout).collect()
            errors.extend(universe.get("errors") or [])
            for item in (universe.get("top") or [])[:40]:
                product = str(item.get("product") or "")
                edge = float(item.get("score") or 0.0)
                confidence = min(0.95, 0.35 + abs(edge) * 0.6)
                opportunities.append(
                    Opportunity(
                        venue="crypto",
                        instrument=product,
                        title=f"Coinbase spot {product}",
                        score=edge,
                        confidence=confidence,
                        execution_ready=(product.upper() in tradable_products),
                        details={
                            "price": float(item.get("price") or 0.0),
                            "return_5m": float(item.get("return_5m") or 0.0),
                            "return_1h": float(item.get("return_1h") or 0.0),
                            "return_6h": float(item.get("return_6h") or 0.0),
                            "volume_ratio": float(item.get("volume_ratio") or 0.0),
                            "spread_bps": float(item.get("spread_bps") or 0.0),
                            "account_tradable": product.upper() in tradable_products,
                            "note": (
                                "Coinbase-wide research candidate. LIVE means the "
                                "connected Advanced account currently exposes this "
                                "product; otherwise it remains research-only."
                            ),
                        },
                        priority=self._edge_priority(edge, confidence),
                        score_kind="directional_edge",
                    )
                )
        except Exception as exc:
            errors.append(f"coinbase_universe: {exc}")

        try:
            opportunities.extend(self._prediction_markets())
        except Exception as exc:
            errors.append(f"predictions: {exc}")

        # Priority means "worth looking at next", not "guaranteed return". Crypto
        # uses directional edge/confidence; predictions use market quality until a
        # real independent probability model exists.
        opportunities.sort(key=lambda item: item.priority, reverse=True)

        caps = capability_snapshot()
        return {
            "capabilities": caps,
            "opportunities": [item.as_dict() for item in opportunities],
            "prediction_scan": dict(self._prediction_scan_meta),
            "errors": errors,
        }

    @staticmethod
    def _edge_priority(score: float, confidence: float) -> float:
        return max(0.0, min(1.0, confidence * (0.45 + 0.55 * abs(score))))

    def _prediction_markets(self) -> list[Opportunity]:
        markets = self._all_open_prediction_markets()
        category_counts: dict[str, int] = {}
        sports_counts: dict[str, int] = {}
        out: list[Opportunity] = []

        for item in markets:
            yes_bid = self._number(item, "yes_bid_dollars")
            yes_ask = self._number(item, "yes_ask_dollars")
            if yes_bid <= 0 or yes_ask <= 0 or yes_ask < yes_bid:
                continue

            midpoint = (yes_bid + yes_ask) / 2.0
            spread = yes_ask - yes_bid
            volume = self._number(item, "volume_24h_fp") or self._number(item, "volume_fp")
            open_interest = self._number(item, "open_interest_fp")
            category, sport = self._prediction_category(item)
            category_counts[category] = category_counts.get(category, 0) + 1
            if sport:
                sports_counts[sport] = sports_counts.get(sport, 0) + 1

            quality = self._prediction_market_quality(
                spread=spread,
                volume=volume,
                open_interest=open_interest,
                close_time=item.get("close_time"),
            )

            out.append(
                Opportunity(
                    venue="predictions",
                    instrument=str(item.get("ticker") or ""),
                    title=str(
                        item.get("title")
                        or item.get("subtitle")
                        or item.get("ticker")
                        or "Prediction market"
                    ),
                    score=0.0,
                    confidence=quality,
                    execution_ready=False,
                    details={
                        "category": category,
                        "sport": sport,
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "market_probability_mid": midpoint,
                        "no_probability_mid": 1.0 - midpoint,
                        "spread": spread,
                        "volume": volume,
                        "open_interest": open_interest,
                        "close_time": item.get("close_time"),
                        "event_ticker": item.get("event_ticker"),
                        "series_ticker": item.get("series_ticker"),
                        "market_quality_score": quality,
                        "directional_edge": None,
                        "edge_status": "UNMODELED",
                        "note": (
                            "Live public market discovery and quality ranking. "
                            "Price is market-implied probability, not an AIscend edge "
                            "estimate until an independent event model is attached."
                        ),
                    },
                    priority=quality,
                    score_kind="market_quality",
                )
            )

        out.sort(key=lambda item: item.priority, reverse=True)
        self._prediction_scan_meta.update(
            {
                "quoted_markets": len(out),
                "category_counts": category_counts,
                "sports_counts": sports_counts,
            }
        )
        # Return a broad research set. The browser can display only the highest
        # ranked subset without preventing the scanner from sniffing the rest.
        return out[:100]

    def _all_open_prediction_markets(self) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        cursor = ""
        pages = 0

        while pages < self.max_prediction_pages:
            params = {"status": "open", "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            payload = self._get_json("/markets?" + urllib.parse.urlencode(params))
            page = payload.get("markets") or []
            if not isinstance(page, list):
                break
            markets.extend(item for item in page if isinstance(item, dict))
            pages += 1
            cursor = str(payload.get("cursor") or "").strip()
            if not cursor or not page:
                break

        self._prediction_scan_meta = {
            "open_markets_seen": len(markets),
            "pages_scanned": pages,
            "truncated": bool(cursor),
        }
        return markets

    @classmethod
    def _prediction_category(
        cls,
        item: dict[str, Any],
    ) -> tuple[str, str | None]:
        supplied = str(item.get("category") or "").strip().lower()
        text = " ".join(
            str(item.get(key) or "")
            for key in (
                "ticker",
                "title",
                "subtitle",
                "event_ticker",
                "series_ticker",
                "category",
            )
        ).upper()

        for sport, patterns in cls.SPORTS_PATTERNS:
            if any(pattern in text for pattern in patterns):
                return "sports", sport

        if supplied in {"sports", "sport"}:
            return "sports", "OTHER"

        buckets = (
            ("politics", ("ELECTION", "PRESIDENT", "CONGRESS", "SENATE", "GOVERNOR", "POLITIC")),
            ("crypto", ("BITCOIN", "BTC", "ETHEREUM", "ETH", "CRYPTO", "SOLANA", "SOL")),
            ("weather", ("WEATHER", "TEMPERATURE", "HURRICANE", "SNOW", "RAIN")),
            ("economics", ("FED", "CPI", "GDP", "INFLATION", "RATE CUT", "JOBS", "UNEMPLOYMENT")),
            ("culture", ("OSCARS", "GRAMMY", "MOVIE", "MUSIC", "TV", "CELEBRITY")),
        )
        for category, patterns in buckets:
            if any(pattern in text for pattern in patterns):
                return category, None

        if supplied:
            return re.sub(r"[^a-z0-9_]+", "_", supplied).strip("_") or "other", None
        return "other", None

    @staticmethod
    def _prediction_market_quality(
        *,
        spread: float,
        volume: float,
        open_interest: float,
        close_time: Any,
    ) -> float:
        liquidity = max(0.0, volume) + max(0.0, open_interest)
        liquidity_score = min(1.0, math.log10(1.0 + liquidity) / 5.0)
        spread_score = 1.0 - min(1.0, max(0.0, spread) / 0.20)

        urgency = 0.45
        if close_time:
            try:
                close = datetime.fromisoformat(str(close_time).replace("Z", "+00:00"))
                if close.tzinfo is None:
                    close = close.replace(tzinfo=timezone.utc)
                hours = max(
                    0.0,
                    (close.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
                    / 3600.0,
                )
                urgency = (
                    1.0
                    if hours <= 24
                    else 0.85
                    if hours <= 72
                    else 0.70
                    if hours <= 168
                    else 0.55
                    if hours <= 720
                    else 0.40
                )
            except Exception:
                urgency = 0.45

        return max(
            0.0,
            min(
                1.0,
                0.55 * liquidity_score
                + 0.30 * spread_score
                + 0.15 * urgency,
            ),
        )

    @staticmethod
    def _number(item: dict[str, Any], key: str) -> float:
        try:
            return float(item.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            KALSHI_PUBLIC + path,
            headers={
                "Accept": "application/json",
                "User-Agent": "AIscend/0.17 opportunity-universe",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
