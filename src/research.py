from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from typing import Any

from .core import Decision
from .sentiment import HumanSignalResearch, HumanSignals


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
    technical_score: float
    human_score: float
    crowd_edge: float
    event_intensity: float
    confidence: float
    prediction: str
    eth: AssetSignals | None
    btc: AssetSignals | None
    human: HumanSignals | None
    thesis: str
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["eth"] = self.eth.as_dict() if self.eth else None
        data["btc"] = self.btc.as_dict() if self.btc else None
        data["human"] = self.human.as_dict() if self.human else None
        return data


class MarketResearch:
    """
    Fuses price action with human behavior.

    The score is deliberately context-sensitive: FOMO can be continuation fuel,
    exhaustion, or a contrarian setup depending on momentum and participation.
    Politics and strange news affect both direction and uncertainty rather than
    being treated as automatically bullish or bearish.
    """

    def __init__(
        self,
        timeout: float = 8.0,
        human_researcher: HumanSignalResearch | None = None,
    ) -> None:
        self.timeout = timeout
        self.human_researcher = human_researcher or HumanSignalResearch(
            timeout=min(timeout, 7.0)
        )

    def collect(self) -> ResearchPack:
        errors: list[str] = []
        eth = self._signals("ETH-USD", errors)
        btc = self._signals("BTC-USD", errors)

        try:
            human = self.human_researcher.collect()
            errors.extend(human.errors)
        except Exception as exc:
            human = None
            errors.append(f"human_signals: {exc}")

        if eth is None:
            return ResearchPack(
                time.time(),
                False,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                "UNKNOWN",
                None,
                btc,
                human,
                "ETH market research unavailable. No live thesis.",
                tuple(errors),
            )

        def squash(x: float, scale: float) -> float:
            return math.tanh(x / scale) if scale > 0 else 0.0

        technical_score = (
            0.18 * squash(eth.return_5m, 0.004)
            + 0.28 * squash(eth.return_1h, 0.012)
            + 0.24 * squash(eth.return_6h, 0.03)
            + 0.10 * squash(eth.volume_ratio - 1.0, 0.75)
        )
        if btc is not None:
            technical_score += (
                0.08 * squash(btc.return_1h, 0.012)
                + 0.08 * squash(btc.return_6h, 0.03)
            )
        technical_score = max(-1.0, min(1.0, technical_score))

        momentum_5m = squash(eth.return_5m, 0.004)
        momentum_1h = squash(eth.return_1h, 0.012)
        volume_heat = max(0.0, squash(eth.volume_ratio - 1.0, 0.75))

        human_score = 0.0
        crowd_edge = 0.0
        event_intensity = 0.0
        fomo = 50.0

        if human is not None and human.available:
            technical_heat = 50.0 + 50.0 * max(
                -1.0,
                min(
                    1.0,
                    0.46 * momentum_5m
                    + 0.34 * momentum_1h
                    + 0.20 * volume_heat,
                ),
            )
            fomo = max(
                0.0,
                min(100.0, 0.68 * human.raw_fomo_index + 0.32 * technical_heat),
            )
            human = replace(
                human,
                fomo_index=fomo,
                crowd_regime=HumanSignalResearch._crowd_regime(fomo),
            )

            crowd_edge = self._crowd_edge(
                fomo=fomo,
                momentum_5m=momentum_5m,
                momentum_1h=momentum_1h,
                volume_heat=volume_heat,
            )
            fg_delta = max(-1.0, min(1.0, human.fear_greed_change_1d / 20.0))
            human_score = (
                0.28 * human.news_sentiment
                + 0.30 * human.social_sentiment
                + 0.12 * human.politics_sentiment
                + 0.22 * crowd_edge
                + 0.08 * fg_delta
            )
            human_score = max(-1.0, min(1.0, human_score))
            event_intensity = max(human.politics_risk, human.weirdness)

        human_weight = 0.26 + (0.12 * event_intensity if human and human.available else 0.0)
        if human is None or not human.available:
            human_weight = 0.0
        technical_weight = 1.0 - human_weight
        score = (
            technical_weight * technical_score
            + human_weight * human_score
        )
        score = max(-1.0, min(1.0, score))

        friction_penalty = min(0.25, eth.spread_bps / 100.0)
        vol_penalty = min(0.20, max(0.0, eth.realized_vol_1h - 0.02) * 4.0)

        alignment_bonus = 0.0
        disagreement_penalty = 0.0
        if human is not None and human.available:
            if technical_score * human_score > 0:
                alignment_bonus = 0.06 * min(abs(technical_score), abs(human_score))
            elif abs(human_score) > 0.10 and abs(technical_score) > 0.10:
                disagreement_penalty = 0.06 * event_intensity + 0.03

        confidence = max(
            0.0,
            min(
                1.0,
                abs(score)
                + alignment_bonus
                - friction_penalty
                - vol_penalty
                - disagreement_penalty,
            ),
        )
        prediction = (
            "BULLISH"
            if score >= 0.16
            else "BEARISH"
            if score <= -0.16
            else "NEUTRAL"
        )

        btc_note = ""
        if btc is not None:
            btc_note = f" BTC 1h {btc.return_1h:+.2%}, 6h {btc.return_6h:+.2%}."

        human_note = ""
        if human is not None and human.available:
            human_note = (
                f" Crowd {human.crowd_regime} FOMO {human.fomo_index:.0f}/100, "
                f"news {human.news_sentiment:+.2f}, social {human.social_sentiment:+.2f}, "
                f"politics {human.politics_sentiment:+.2f}, event {event_intensity:.2f}, "
                f"crowd edge {crowd_edge:+.2f}."
            )

        thesis = (
            f"ETH 5m {eth.return_5m:+.2%}, 1h {eth.return_1h:+.2%}, "
            f"6h {eth.return_6h:+.2%}, vol {eth.realized_vol_1h:.2%}, "
            f"volume {eth.volume_ratio:.2f}x, spread {eth.spread_bps:.2f} bps."
            + btc_note
            + human_note
            + f" Technical {technical_score:+.3f}, human {human_score:+.3f}, "
              f"composite {score:+.3f}, confidence {confidence:.2f}."
        )

        return ResearchPack(
            time.time(),
            True,
            score,
            technical_score,
            human_score,
            crowd_edge,
            event_intensity,
            confidence,
            prediction,
            eth,
            btc,
            human,
            thesis,
            tuple(errors),
        )

    @staticmethod
    def _crowd_edge(
        fomo: float,
        momentum_5m: float,
        momentum_1h: float,
        volume_heat: float,
    ) -> float:
        """
        FOMO is not hard-coded as bad.

        - Hot crowd + confirmed momentum/volume: continuation fuel.
        - Hot crowd + short-term reversal: exhaustion risk.
        - Extreme fear + positive reversal: contrarian opportunity.
        - Extreme fear + continued weakness: stay defensive.
        """
        centered = (fomo - 50.0) / 50.0

        if fomo >= 68.0:
            heat = abs(centered)
            if momentum_1h > 0 and momentum_5m >= 0:
                return max(
                    -1.0,
                    min(
                        1.0,
                        heat
                        * (
                            0.55 * momentum_1h
                            + 0.25 * momentum_5m
                            + 0.20 * volume_heat
                        ),
                    ),
                )
            if momentum_5m < 0:
                return max(
                    -1.0,
                    -heat
                    * min(
                        1.0,
                        0.65 * abs(momentum_5m)
                        + 0.35 * max(0.0, -momentum_1h),
                    ),
                )

        if fomo <= 30.0:
            fear = (30.0 - fomo) / 30.0
            if momentum_5m > 0:
                return min(
                    1.0,
                    fear
                    * (
                        0.65 * momentum_5m
                        + 0.35 * max(0.0, momentum_1h)
                    ),
                )
            return max(
                -1.0,
                -fear
                * min(
                    1.0,
                    0.55 * abs(min(0.0, momentum_5m))
                    + 0.45 * abs(min(0.0, momentum_1h)),
                ),
            )

        return max(-1.0, min(1.0, 0.50 * centered * momentum_1h))

    def _signals(self, product: str, errors: list[str]) -> AssetSignals | None:
        try:
            candles = self._get_json(
                f"/products/{urllib.parse.quote(product)}/candles?granularity=300"
            )
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
            recent_returns = [
                self._pct(closes[i], closes[i - 1])
                for i in range(len(closes) - 11, len(closes))
            ]
            realized_vol_1h = (
                statistics.pstdev(recent_returns) * math.sqrt(12)
                if len(recent_returns) > 1
                else 0.0
            )
            baseline = statistics.median(volumes[-13:-1]) or 1.0
            volume_ratio = volumes[-1] / baseline
            bid = float(book["bids"][0][0])
            ask = float(book["asks"][0][0])
            mid = (bid + ask) / 2.0
            spread_bps = ((ask - bid) / mid) * 10000.0 if mid else 0.0
            return AssetSignals(
                product,
                price,
                return_5m,
                return_1h,
                return_6h,
                realized_vol_1h,
                spread_bps,
                volume_ratio,
            )
        except Exception as exc:
            errors.append(f"{product}: {exc}")
            return None

    def _get_json(self, path: str) -> Any:
        request = urllib.request.Request(
            COINBASE_PUBLIC + path,
            headers={
                "Accept": "application/json",
                "User-Agent": "AIscend/0.10 market-research",
            },
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
            return Decision(
                "HOLD",
                None,
                0.0,
                "Research unavailable, so no blind live trade.",
            )

        score = float(research.get("composite_score", 0.0))
        confidence = float(research.get("confidence", 0.0))
        thesis = str(research.get("thesis", "Market research"))
        cash = float(snapshot.get("cash", 0.0))
        positions = snapshot.get("positions") or {}
        has_weth = float(positions.get("WETH", {}).get("qty", 0.0)) > 0

        human = research.get("human") or {}
        fomo = float(human.get("fomo_index", 50.0))
        crowd_regime = str(human.get("crowd_regime", "UNKNOWN"))
        eth = research.get("eth") or {}
        ret_5m = float(eth.get("return_5m", 0.0))
        ret_1h = float(eth.get("return_1h", 0.0))
        volume_ratio = float(eth.get("volume_ratio", 1.0))

        buy_threshold = 0.16
        sell_threshold = -0.16

        # Hot crowds are tradable when price/participation confirm them.
        if 68 <= fomo < 88 and ret_5m >= 0 and ret_1h > 0 and volume_ratio >= 1.0:
            buy_threshold = 0.12

        # Euphoria can still be bought, but only with stronger confirmation.
        if fomo >= 88:
            if ret_5m > 0 and ret_1h > 0 and volume_ratio >= 1.20:
                buy_threshold = 0.14
            else:
                buy_threshold = 0.24
            if ret_5m < 0:
                sell_threshold = -0.08

        # Fear plus a real reversal is a contrarian setup, not an automatic veto.
        if fomo <= 25 and ret_5m > 0 and ret_1h >= -0.01:
            buy_threshold = 0.10
        elif fomo <= 25 and ret_5m < 0:
            sell_threshold = -0.12

        if has_weth and score <= sell_threshold:
            fraction = min(1.0, max(0.25, 0.30 + abs(score) * 0.70))
            return Decision(
                "SELL",
                "WETH",
                fraction,
                f"Research regime bearish at {score:+.3f}; crowd {crowd_regime} "
                f"({fomo:.0f}/100). {thesis}",
            )

        if cash > 0.01 and score >= buy_threshold:
            fraction = min(0.85, max(0.15, 0.15 + confidence * 0.70))
            return Decision(
                "BUY",
                "WETH",
                fraction,
                f"Research edge clears {buy_threshold:+.2f}; crowd {crowd_regime} "
                f"({fomo:.0f}/100). {thesis}",
            )

        return Decision(
            "HOLD",
            None,
            0.0,
            f"No trade clears context-aware threshold. Crowd {crowd_regime} "
            f"({fomo:.0f}/100). {thesis}",
        )
