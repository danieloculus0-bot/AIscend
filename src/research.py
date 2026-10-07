from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .core import Decision


COINBASE_PUBLIC = "https://api.exchange.coinbase.com"


@dataclass(frozen=True)
class AssetSignals:
    product: str
    price: float
    return_5m: float
    return_1h: float
    return_6h: float
    realized_vol_1h: float
    spread_bps: float
    volume_ratio: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchPack:
    generated_at: float
    available: bool
    composite_score: float
    confidence: float
    prediction: str
    eth: AssetSignals | None
    btc: AssetSignals | None
    thesis: str
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["eth"] = self.eth.as_dict() if self.eth else None
        data["btc"] = self.btc.as_dict() if self.btc else None
        return data


class MarketResearch:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def collect(self) -> ResearchPack:
        errors: list[str] = []
        eth = self._signals("ETH-USD", errors)
        btc = self._signals("BTC-USD", errors)
        if eth is None:
            return ResearchPack(time.time(), False, 0.0, 0.0, "UNKNOWN", None, btc,
                "ETH market research unavailable. No live thesis.", tuple(errors))

        def squash(x: float, scale: float) -> float:
            return math.tanh(x / scale) if scale > 0 else 0.0

        score = (
            0.18 * squash(eth.return_5m, 0.004)
            + 0.28 * squash(eth.return_1h, 0.012)
            + 0.24 * squash(eth.return_6h, 0.03)
            + 0.10 * squash(eth.volume_ratio - 1.0, 0.75)
        )
        if btc is not None:
            score += (
                0.08 * squash(btc.return_1h, 0.012)
                + 0.08 * squash(btc.return_6h, 0.03)
            )

        score = max(-1.0, min(1.0, score))
        friction_penalty = min(0.25, eth.spread_bps / 100.0)
        vol_penalty = min(0.20, max(0.0, eth.realized_vol_1h - 0.02) * 4.0)
        confidence = max(0.0, min(1.0, abs(score) - friction_penalty - vol_penalty))
        prediction = "BULLISH" if score >= 0.18 else "BEARISH" if score <= -0.18 else "NEUTRAL"

        btc_note = ""
        if btc is not None:
            btc_note = f" BTC 1h {btc.return_1h:+.2%}, 6h {btc.return_6h:+.2%}."

        thesis = (
            f"ETH 5m {eth.return_5m:+.2%}, 1h {eth.return_1h:+.2%}, "
            f"6h {eth.return_6h:+.2%}, vol {eth.realized_vol_1h:.2%}, "
            f"volume {eth.volume_ratio:.2f}x, spread {eth.spread_bps:.2f} bps."
            + btc_note
            + f" Composite {score:+.3f}, confidence {confidence:.2f}."
        )
        return ResearchPack(time.time(), True, score, confidence, prediction, eth, btc, thesis, tuple(errors))

    def _signals(self, product: str, errors: list[str]) -> AssetSignals | None:
        try:
            candles = self._get_json(f"/products/{urllib.parse.quote(product)}/candles?granularity=300")
            ticker = self._get_json(f"/products/{urllib.parse.quote(product)}/ticker")
            book = self._get_json(f"/products/{urllib.parse.quote(product)}/book?level=1")
            if not isinstance(candles, list) or len(candles) < 72:
                raise RuntimeError("insufficient candle history")
            ordered = list(reversed(candles))
            closes = [float(row[4]) for row in ordered]
            volumes = [float(row[5]) for row in ordered]
            price = float(ticker["price"])
            return_5m = self._pct(closes[-1], closes[-2])
            return_1h = self._pct(closes[-1], closes[-13])
            return_6h = self._pct(closes[-1], closes[-72])
            recent_returns = [self._pct(closes[i], closes[i-1]) for i in range(len(closes)-11, len(closes))]
            realized_vol_1h = statistics.pstdev(recent_returns) * math.sqrt(12) if len(recent_returns) > 1 else 0.0
            baseline = statistics.median(volumes[-13:-1]) or 1.0
            volume_ratio = volumes[-1] / baseline
            bid = float(book["bids"][0][0]); ask = float(book["asks"][0][0]); mid = (bid + ask) / 2.0
            spread_bps = ((ask - bid) / mid) * 10000.0 if mid else 0.0
            return AssetSignals(product, price, return_5m, return_1h, return_6h, realized_vol_1h, spread_bps, volume_ratio)
        except Exception as exc:
            errors.append(f"{product}: {exc}")
            return None

    def _get_json(self, path: str) -> Any:
        request = urllib.request.Request(
            COINBASE_PUBLIC + path,
            headers={"Accept":"application/json","User-Agent":"AIscend/0.8 market-research"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _pct(new: float, old: float) -> float:
        return (new / old) - 1.0 if old else 0.0


class ResearchDecider:
    def decide(self, snapshot: dict[str, Any]) -> Decision:
        research = snapshot.get("research") or {}
        if not research.get("available"):
            return Decision("HOLD", None, 0.0, "Research unavailable, so no blind live trade.")

        score = float(research.get("composite_score", 0.0))
        confidence = float(research.get("confidence", 0.0))
        thesis = str(research.get("thesis", "Market research"))
        cash = float(snapshot.get("cash", 0.0))
        positions = snapshot.get("positions") or {}
        has_weth = float(positions.get("WETH", {}).get("qty", 0.0)) > 0

        if has_weth and score <= -0.18:
            fraction = min(1.0, max(0.25, 0.30 + abs(score) * 0.70))
            return Decision("SELL", "WETH", fraction, f"Research regime turned bearish. {thesis}")

        if cash > 0.01 and score >= 0.18:
            fraction = min(0.80, max(0.15, 0.15 + confidence * 0.65))
            return Decision("BUY", "WETH", fraction, f"Research regime supports ETH risk. {thesis}")

        return Decision("HOLD", None, 0.0, f"No trade clears research threshold. {thesis}")
