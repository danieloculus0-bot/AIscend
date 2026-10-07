from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any


ALT_FNG = "https://api.alternative.me/fng/"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS = "https://news.google.com/rss/search"
REDDIT = "https://www.reddit.com"
BLUESKY = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"

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
    source_status: dict[str, str]
    top_headlines: tuple[str, ...]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HumanSignalResearch:
    """Crowd psychology with redundant public feeds and visible source health."""

    _cache_lock = threading.Lock()
    _cached_pack: HumanSignals | None = None
    _cache_until = 0.0

    def __init__(self, timeout: float = 7.0, cache_seconds: float = 120.0) -> None:
        self.timeout = timeout
        self.cache_seconds = max(0.0, float(cache_seconds))

    def collect(self) -> HumanSignals:
        cls = type(self)
        now = time.time()
        with cls._cache_lock:
            if cls._cached_pack is not None and now < cls._cache_until:
                return cls._cached_pack
        pack = self._collect_uncached()
        with cls._cache_lock:
            cls._cached_pack = pack
            cls._cache_until = time.time() + self.cache_seconds
        return pack

    def _collect_uncached(self) -> HumanSignals:
        errors: list[str] = []
        status: dict[str, str] = {}
        fng_value: float | None = None
        fng_class = "UNKNOWN"
        fng_change = 0.0
        news_titles: list[str] = []
        politics_titles: list[str] = []
        weird_titles: list[str] = []
        social_posts: list[dict[str, Any]] = []

        news_specs = {
            "news": (
                '(bitcoin OR ethereum OR crypto OR cryptocurrency)',
                'bitcoin OR ethereum OR crypto OR cryptocurrency',
                60,
            ),
            "politics": (
                '(election OR tariff OR sanctions OR regulation OR "federal reserve" OR war)',
                'markets election tariff sanctions regulation federal reserve war',
                60,
            ),
            "weird": (
                '(cyberattack OR outage OR explosion OR earthquake OR hurricane OR coup OR "bank run")',
                'markets cyberattack outage explosion earthquake hurricane coup bank run',
                40,
            ),
        }

        def fetch_fng() -> tuple[str, Any, str]:
            payload = self._get_json(ALT_FNG + "?limit=2&format=json")
            return "fear_greed", payload, "Alternative.me"

        def fetch_news(label: str, spec: tuple[str, str, int]) -> tuple[str, Any, str]:
            gdelt_query, rss_query, limit = spec
            try:
                titles = self._gdelt_titles(gdelt_query, "6h", limit)
                if titles:
                    return label, titles, f"GDELT ({len(titles)})"
            except Exception as exc:
                errors.append(f"{label}/gdelt: {exc}")
            titles = self._google_news_titles(rss_query, limit)
            return label, titles, f"Google News fallback ({len(titles)})"

        def fetch_reddit(subreddit: str) -> tuple[str, Any, str]:
            try:
                posts = self._reddit_json(subreddit, 18)
                if posts:
                    return f"reddit/{subreddit}", posts, "Reddit JSON"
            except Exception as exc:
                errors.append(f"reddit/{subreddit}/json: {exc}")
            posts = self._reddit_rss(subreddit, 18)
            return f"reddit/{subreddit}", posts, "Reddit RSS fallback"

        def fetch_bluesky(query: str) -> tuple[str, Any, str]:
            posts = self._bluesky_search(query, 25)
            return f"bluesky/{query}", posts, "Bluesky public AppView"

        futures: dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="aiscend-signal") as pool:
            futures[pool.submit(fetch_fng)] = "fear_greed"
            for label, spec in news_specs.items():
                futures[pool.submit(fetch_news, label, spec)] = label
            for subreddit in ("CryptoCurrency", "Bitcoin", "ethereum", "wallstreetbets", "stocks"):
                futures[pool.submit(fetch_reddit, subreddit)] = f"reddit/{subreddit}"
            for query in ("bitcoin", "ethereum", "crypto"):
                futures[pool.submit(fetch_bluesky, query)] = f"bluesky/{query}"

            for future in as_completed(futures):
                label = futures[future]
                try:
                    result_label, payload, source_name = future.result()
                    if result_label == "fear_greed":
                        rows = payload.get("data") or []
                        if rows:
                            fng_value = self._num(rows[0].get("value"))
                            fng_class = str(
                                rows[0].get("value_classification") or "UNKNOWN"
                            ).upper()
                            if len(rows) > 1:
                                fng_change = fng_value - self._num(rows[1].get("value"))
                        status["fear_greed"] = source_name
                    elif result_label == "news":
                        news_titles = payload
                        status["news"] = source_name
                    elif result_label == "politics":
                        politics_titles = payload
                        status["politics"] = source_name
                    elif result_label == "weird":
                        weird_titles = payload
                        status["weird"] = source_name
                    elif result_label.startswith("reddit/"):
                        social_posts.extend(payload)
                        status["reddit"] = source_name
                    elif result_label.startswith("bluesky/"):
                        social_posts.extend(payload)
                        status["bluesky"] = source_name
                except Exception as exc:
                    errors.append(f"{label}: {exc}")

        social_posts = self._dedupe_posts(social_posts)
        social_titles = [
            str(p.get("title") or "") for p in social_posts if p.get("title")
        ]

        news_sentiment = self._sentiment(news_titles)
        social_sentiment = self._sentiment(social_titles)
        politics_sentiment = self._sentiment(politics_titles)
        politics_risk = self._keyword_density(
            politics_titles, PANIC_WORDS | NEGATIVE_WORDS
        )
        weirdness = self._keyword_density(
            weird_titles,
            PANIC_WORDS | {"outage", "earthquake", "explosion", "coup"},
        )
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

        headlines = tuple(
            self._dedupe_strings(news_titles + politics_titles + weird_titles)[:12]
        )
        counts = {
            "news": len(news_titles),
            "politics": len(politics_titles),
            "weird": len(weird_titles),
            "social": len(social_titles),
        }
        available = bool(
            fng_value is not None
            or news_titles
            or politics_titles
            or weird_titles
            or social_titles
        )

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
            crowd_regime=self._crowd_regime(raw_fomo),
            source_counts=counts,
            source_status=status,
            top_headlines=headlines,
            errors=tuple(errors[-20:]),
        )

    def _gdelt_titles(self, query: str, timespan: str, maxrecords: int) -> list[str]:
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
        return self._dedupe_strings(
            [
                str(row.get("title") or "").strip()
                for row in (payload.get("articles") or [])
                if row.get("title")
            ]
        )

    def _google_news_titles(self, query: str, limit: int) -> list[str]:
        params = urllib.parse.urlencode(
            {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        )
        request = urllib.request.Request(
            f"{GOOGLE_NEWS}?{params}",
            headers={"User-Agent": "Mozilla/5.0 AIscend/0.12"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            root = ET.fromstring(response.read())
        titles = [
            (node.text or "").strip()
            for node in root.findall(".//item/title")
            if (node.text or "").strip()
        ]
        return self._dedupe_strings(titles)[:limit]

    def _reddit_json(self, subreddit: str, limit: int) -> list[dict[str, Any]]:
        url = (
            f"{REDDIT}/r/{urllib.parse.quote(subreddit)}/hot.json"
            f"?limit={int(limit)}&raw_json=1"
        )
        payload = self._get_json(url)
        out: list[dict[str, Any]] = []
        for child in ((payload.get("data") or {}).get("children") or []):
            data = child.get("data") or {}
            if data.get("stickied"):
                continue
            out.append(
                {
                    "title": data.get("title"),
                    "score": self._num(data.get("score")),
                    "comments": self._num(data.get("num_comments")),
                    "upvote_ratio": self._num(data.get("upvote_ratio")),
                    "source": "reddit",
                }
            )
        return out

    def _reddit_rss(self, subreddit: str, limit: int) -> list[dict[str, Any]]:
        url = f"{REDDIT}/r/{urllib.parse.quote(subreddit)}/hot/.rss"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/atom+xml,application/rss+xml,text/xml",
                "User-Agent": "Mozilla/5.0 (compatible; AIscend/0.12; market research)",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            root = ET.fromstring(response.read())
        titles: list[str] = []
        for node in root.findall(".//{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title"):
            if node.text:
                titles.append(node.text.strip())
        if not titles:
            titles = [
                (node.text or "").strip()
                for node in root.findall(".//item/title")
                if (node.text or "").strip()
            ]
        return [
            {
                "title": title,
                "score": 0.0,
                "comments": 0.0,
                "upvote_ratio": 0.5,
                "source": "reddit_rss",
            }
            for title in self._dedupe_strings(titles)[:limit]
        ]

    def _bluesky_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode(
            {"q": query, "limit": min(100, int(limit)), "sort": "latest"}
        )
        payload = self._get_json(f"{BLUESKY}?{params}")
        out: list[dict[str, Any]] = []
        for post in payload.get("posts") or []:
            record = post.get("record") or {}
            text = str(record.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "title": text[:500],
                    "score": self._num(post.get("likeCount"))
                    + 2.0 * self._num(post.get("repostCount")),
                    "comments": self._num(post.get("replyCount")),
                    "upvote_ratio": 0.5,
                    "source": "bluesky",
                }
            )
        return out

    def _get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; AIscend/0.12; market research)",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = " ".join(str(value).split()).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                out.append(text)
        return out

    @classmethod
    def _dedupe_posts(cls, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for post in posts:
            title = " ".join(str(post.get("title") or "").split()).strip()
            key = title.lower()
            if title and key not in seen:
                seen.add(key)
                item = dict(post)
                item["title"] = title
                out.append(item)
        return out

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

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
        hits = sum(
            1
            for text in texts
            if any(term in text.lower() for term in lexicon)
        )
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
