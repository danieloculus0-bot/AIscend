from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .research import MarketResearch
from .venues import capability_snapshot


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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpportunityUniverse:
    """Normalizes research across all first-launch market lanes."""

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def collect(self) -> dict[str, Any]:
        errors: list[str] = []
        opportunities: list[Opportunity] = []

        crypto = MarketResearch(timeout=self.timeout).collect()
        if crypto.available and crypto.eth is not None:
            opportunities.append(
                Opportunity(
                    venue="crypto",
                    instrument="ETH/USDC",
                    title="Ethereum spot via WETH/USDC on Base",
                    score=float(crypto.composite_score),
                    confidence=float(crypto.confidence),
                    execution_ready=True,
                    details={
                        "prediction": crypto.prediction,
                        "thesis": crypto.thesis,
                        "price": crypto.eth.price,
                    },
                )
            )
        else:
            errors.extend(crypto.errors)

        try:
            opportunities.extend(self._prediction_markets())
        except Exception as exc:
            errors.append(f"predictions: {exc}")

        caps = capability_snapshot()
        return {
            "capabilities": caps,
            "opportunities": [item.as_dict() for item in opportunities],
            "errors": errors,
        }

    def _prediction_markets(self) -> list[Opportunity]:
        payload = self._get_json("/markets?status=open&limit=100")
        markets = payload.get("markets") or []

        def number(item: dict[str, Any], key: str) -> float:
            try:
                return float(item.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        ranked = sorted(
            markets,
            key=lambda item: (
                number(item, "volume_24h_fp"),
                number(item, "volume_fp"),
                number(item, "open_interest_fp"),
            ),
            reverse=True,
        )

        out: list[Opportunity] = []
        for item in ranked:
            yes_bid = number(item, "yes_bid_dollars")
            yes_ask = number(item, "yes_ask_dollars")
            if yes_bid <= 0 or yes_ask <= 0 or yes_ask < yes_bid:
                continue

            midpoint = (yes_bid + yes_ask) / 2.0
            spread = yes_ask - yes_bid
            volume = number(item, "volume_24h_fp") or number(item, "volume_fp")
            open_interest = number(item, "open_interest_fp")

            # This is a liquidity/attention rank, not an assertion that the market
            # probability is wrong. Independent edge estimation comes later.
            liquidity_score = min(
                1.0,
                (volume / 50000.0) + (open_interest / 100000.0),
            )
            confidence = max(0.0, min(1.0, liquidity_score * (1.0 - min(1.0, spread))))

            out.append(
                Opportunity(
                    venue="predictions",
                    instrument=str(item.get("ticker") or ""),
                    title=str(item.get("title") or item.get("ticker") or "Prediction market"),
                    score=0.0,
                    confidence=confidence,
                    execution_ready=False,
                    details={
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "market_probability_mid": midpoint,
                        "spread": spread,
                        "volume": volume,
                        "open_interest": open_interest,
                        "close_time": item.get("close_time"),
                        "note": (
                            "Public market discovery only. AIscend does not claim an edge "
                            "until an independent prediction model disagrees with market price."
                        ),
                    },
                )
            )
            if len(out) >= 8:
                break

        return out

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            KALSHI_PUBLIC + path,
            headers={
                "Accept": "application/json",
                "User-Agent": "AIscend/0.9 opportunity-universe",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
