from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


ALT_FNG = "https://api.alternative.me/fng/"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
REDDIT = "https://www.reddit.com"

POSITIVE_WORDS = {
    "adoption", "approve", "approved", "approval", "breakout", "bullish", "ceasefire",
    "easing", "gain", "gains", "growth", "inflow", "optimism", "peace", "rally",
    "record high", "rebound", "recovery", "stimulus", "surge", "upgrade", "wins",
}
NEGATIVE_WORDS = {
    "attack", "ban", "bankruptcy", "collapse", "crackdown", "crash", "crisis",
    "default", "emergency", "exploit", "fraud", "hack", "inflation", "investigation",
    "lawsuit", "liquidation", "outage", "plunge", "recession", "sanctions", "selloff",
    "shutdown", "tariff", "tariffs", "war",
}
FOMO_WORDS = {
    "all-time high", "ath", "breakout", "fomo", "frenzy", "jackpot", "meme", "moon",
    "pump", "record", "rally", "rush", "short squeeze", "skyrocket", "surge", "viral",
}
PANIC_WORDS = {
    "attack", "bank run", "bankruptcy", "collapse", "crash", "crisis", "default",
    "emergency", "exploit", "fear", "hack", "liquidation", "panic", "plunge",
    "recession", "rout", "selloff", "war",
}
POLITICAL_WORDS = {
    "congress", "election", "fed", "federal reserve", "government", "iran", "israel",
    "policy", "regulation", "russia", "sanctions", "sec", "senate", "tariff",
    "tariffs", "treasury", "ukraine", "white house", "china",
}


@dataclass(frozen=True)
class HumanSignals:
    generated_at: float
    available: bool
    fear_greed_value: float | None
    fear_greed_classification: str
    fear_greed_change_1d: float
    news_sentiment: float
    social_sentiment: float
    politics_sentiment: float
    politics_risk: float
    weirdness: float
    news_attention: float
    social_attention: float
    raw_fomo_index: float
    fomo_index: float
    crowd_regime: str
    source_counts: dict[str, int]
    top_headlines: tuple[str, ...]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HumanSignalResearch:
    """Collects crowd psychology and event pressure without requiring API keys."""

    def __init__(self, timeout: float = 7.0) -> None:
        self.timeout = timeout

    def collect(self) -> HumanSignals:
        errors: list[str] = []

        fng_value: float | None = None
        fng_class = "UNKNOWN"
        fng_change = 0.0
        try:
            payload = self._get_json(ALT_FNG + "?limit=2&format=json")
            rows = payload.get("data") or []
            if rows:
                fng_value = self._num(rows[0].get("value"))
                fng_class = str(rows[0].get("value_classification") or "UNKNOWN").upper()
                if len(rows) > 1:
                    fng_change = fng_value - self._num(rows[1].get("value"))
        except Exception as exc:
            errors.append(f"fear_greed: {exc}")

        news_titles = self._gdelt_titles(
            '(bitcoin OR ethereum OR crypto OR cryptocurrency OR "digital assets" OR markets OR stocks)',
            "6h",
            60,
            errors,
            "news",
        )
        politics_titles = self._gdelt_titles(
            '(election OR tariff OR tariffs OR sanctions OR regulation OR "federal reserve" OR treasury OR '
            '"white house" OR congress OR war OR ceasefire OR china OR russia OR ukraine OR israel OR iran)',
            "6h",
            60,
            errors,
            "politics",
        )
        weird_titles = self._gdelt_titles(
            '(cyberattack OR hack OR outage OR explosion OR earthquake OR hurricane OR pandemic OR coup OR '
            '"bank run" OR default OR emergency OR assassination OR "supply chain")',
            "6h",
            40,
            errors,
            "weird",
        )

        social_posts: list[dict[str, Any]] = []
        for subreddit in ("CryptoCurrency", "Bitcoin", "ethereum", "wallstreetbets", "stocks"):
            try:
                social_posts.extend(self._reddit_hot(subreddit, 18))
            except Exception as exc:
                errors.append(f"reddit/{subreddit}: {exc}")

        social_titles = [str(p.get("title") or "") for p in social_posts if p.get("title")]

        news_sentiment = self._sentiment(news_titles)
        social_sentiment = self._sentiment(social_titles)
        politics_sentiment = self._sentiment(politics_titles)

        politics_risk = self._keyword_density(politics_titles, PANIC_WORDS | NEGATIVE_WORDS)
        weirdness = self._keyword_density(weird_titles, PANIC_WORDS | {"outage", "earthquake", "explosion", "coup"})
        news_attention = min(1.0, len(news_titles) / 45.0)
        social_attention = self._social_attention(social_posts)

        fg_heat = (fng_value / 100.0) if fng_value is not None else 0.5
        social_fomo = self._keyword_density(social_titles, FOMO_WORDS)
        news_fomo = self._keyword_density(news_titles, FOMO_WORDS)
        raw_fomo = 100.0 * max(
            0.0,
            min(
                1.0,
                0.38 * fg_heat
                + 0.24 * social_fomo
                + 0.18 * news_fomo
                + 0.20 * social_attention,
            ),
        )
        crowd_regime = self._crowd_regime(raw_fomo)

        headlines = tuple((news_titles + politics_titles + weird_titles)[:12])
        counts = {
            "news": len(news_titles),
            "politics": len(politics_titles),
            "weird": len(weird_titles),
            "social": len(social_titles),
        }
        available = bool(fng_value is not None or news_titles or politics_titles or social_titles)

        return HumanSignals(
            generated_at=time.time(),
            available=available,
            fear_greed_value=fng_value,
            fear_greed_classification=fng_class,
            fear_greed_change_1d=fng_change,
            news_sentiment=news_sentiment,
            social_sentiment=social_sentiment,
            politics_sentiment=politics_sentiment,
            politics_risk=politics_risk,
            weirdness=weirdness,
            news_attention=news_attention,
            social_attention=social_attention,
            raw_fomo_index=raw_fomo,
            fomo_index=raw_fomo,
            crowd_regime=crowd_regime,
            source_counts=counts,
            top_headlines=headlines,
            errors=tuple(errors),
        )

    def _gdelt_titles(
        self,
        query: str,
        timespan: str,
        maxrecords: int,
        errors: list[str],
        label: str,
    ) -> list[str]:
        try:
            params = urllib.parse.urlencode(
                {
                    "query": query,
                    "mode": "artlist",
                    "format": "json",
                    "sort": "datedesc",
                    "timespan": timespan,
                    "maxrecords": maxrecords,
                }
            )
            payload = self._get_json(f"{GDELT_DOC}?{params}")
            rows = payload.get("articles") or []
            out: list[str] = []
            seen: set[str] = set()
            for row in rows:
                title = str(row.get("title") or "").strip()
                key = title.lower()
                if title and key not in seen:
                    seen.add(key)
                    out.append(title)
            return out
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            return []

    def _reddit_hot(self, subreddit: str, limit: int) -> list[dict[str, Any]]:
        url = f"{REDDIT}/r/{urllib.parse.quote(subreddit)}/hot.json?limit={int(limit)}&raw_json=1"
        payload = self._get_json(url)
        children = ((payload.get("data") or {}).get("children") or [])
        out: list[dict[str, Any]] = []
        for child in children:
            data = child.get("data") or {}
            if data.get("stickied"):
                continue
            out.append(
                {
                    "title": data.get("title"),
                    "score": self._num(data.get("score")),
                    "comments": self._num(data.get("num_comments")),
                    "upvote_ratio": self._num(data.get("upvote_ratio")),
                }
            )
        return out

    def _get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "AIscend/0.10 sentiment-research (+private research project)",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _contains(text: str, term: str) -> bool:
        return term in text.lower()

    @classmethod
    def _sentiment(cls, texts: list[str]) -> float:
        if not texts:
            return 0.0
        scores: list[float] = []
        for text in texts:
            lower = text.lower()
            pos = sum(1 for word in POSITIVE_WORDS if word in lower)
            neg = sum(1 for word in NEGATIVE_WORDS if word in lower)
            if pos or neg:
                scores.append((pos - neg) / max(1, pos + neg))
        if not scores:
            return 0.0
        return max(-1.0, min(1.0, sum(scores) / len(scores)))

    @staticmethod
    def _keyword_density(texts: list[str], lexicon: set[str]) -> float:
        if not texts:
            return 0.0
        hits = 0
        for text in texts:
            lower = text.lower()
            if any(term in lower for term in lexicon):
                hits += 1
        return max(0.0, min(1.0, hits / max(1, len(texts))))

    @staticmethod
    def _social_attention(posts: list[dict[str, Any]]) -> float:
        if not posts:
            return 0.0
        values: list[float] = []
        for post in posts:
            score = max(0.0, float(post.get("score") or 0.0))
            comments = max(0.0, float(post.get("comments") or 0.0))
            engagement = math.log1p(score + 2.0 * comments)
            values.append(math.tanh(engagement / 8.0))
        return max(0.0, min(1.0, sum(values) / len(values)))

    @staticmethod
    def _crowd_regime(fomo: float) -> str:
        if fomo >= 88:
            return "EUPHORIA"
        if fomo >= 68:
            return "FOMO"
        if fomo >= 45:
            return "ENGAGED"
        if fomo >= 25:
            return "CAUTIOUS"
        return "FEAR"
