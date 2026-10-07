from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any


CLAIM_TYPES = {"OBSERVATION", "INFERENCE", "HYPOTHESIS", "PREDICTION"}


@dataclass(frozen=True)
class BeanClaim:
    claim_type: str
    subject: str
    statement: str
    confidence: float
    source: str
    evidence: tuple[str, ...] = ()
    horizon_seconds: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BeanMemory:
    """Epistemic memory: claims, contradictions, predictions, outcomes and trust."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bean_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                claim_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                statement TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                horizon_seconds INTEGER,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE TABLE IF NOT EXISTS bean_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                due_at REAL NOT NULL,
                subject TEXT NOT NULL,
                horizon_seconds INTEGER NOT NULL,
                start_price REAL NOT NULL,
                predicted_direction INTEGER NOT NULL,
                predicted_strength REAL NOT NULL,
                confidence REAL NOT NULL,
                source_mix_json TEXT NOT NULL DEFAULT '{}',
                resolved_at REAL,
                end_price REAL,
                actual_return REAL,
                hit INTEGER,
                score REAL
            );

            CREATE TABLE IF NOT EXISTS bean_source_stats (
                source TEXT NOT NULL,
                horizon_seconds INTEGER NOT NULL,
                samples INTEGER NOT NULL DEFAULT 0,
                hits INTEGER NOT NULL DEFAULT 0,
                score_sum REAL NOT NULL DEFAULT 0,
                trust REAL NOT NULL DEFAULT 0.5,
                PRIMARY KEY(source, horizon_seconds)
            );

            CREATE TABLE IF NOT EXISTS bean_contradictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                subject TEXT NOT NULL,
                left_source TEXT NOT NULL,
                left_value REAL NOT NULL,
                right_source TEXT NOT NULL,
                right_value REAL NOT NULL,
                severity REAL NOT NULL,
                note TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bean_claim_rollups (
                fingerprint TEXT PRIMARY KEY,
                claim_id INTEGER NOT NULL,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                occurrences INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        self.conn.commit()

    def add_claim(self, claim: BeanClaim, dedupe_seconds: float = 600.0) -> int:
        """
        Store a meaningful claim once, then roll repeated observations into it.

        The live loop may see the same epistemic object every minute. BEAN keeps
        the latest wording/confidence while counting repeats instead of pretending
        each refresh is a brand-new memory.
        """
        kind = claim.claim_type.upper().strip()
        if kind not in CLAIM_TYPES:
            raise ValueError(f"Unsupported BEAN claim type: {kind}")

        now = time.time()
        horizon = int(claim.horizon_seconds) if claim.horizon_seconds is not None else -1
        fingerprint = "|".join(
            (
                kind,
                claim.subject.strip().upper(),
                claim.source.strip().lower(),
                str(horizon),
            )
        )
        row = self.conn.execute(
            """
            SELECT fingerprint,claim_id,last_seen,occurrences
            FROM bean_claim_rollups
            WHERE fingerprint=?
            """,
            (fingerprint,),
        ).fetchone()

        if row is not None and now - float(row["last_seen"]) <= float(dedupe_seconds):
            claim_id = int(row["claim_id"])
            with self.conn:
                self.conn.execute(
                    """
                    UPDATE bean_claims
                    SET ts=?, statement=?, confidence=?, evidence_json=?, status='ACTIVE'
                    WHERE id=?
                    """,
                    (
                        now,
                        claim.statement,
                        max(0.0, min(1.0, float(claim.confidence))),
                        json.dumps(list(claim.evidence), separators=(",", ":")),
                        claim_id,
                    ),
                )
                self.conn.execute(
                    """
                    UPDATE bean_claim_rollups
                    SET last_seen=?, occurrences=occurrences+1
                    WHERE fingerprint=?
                    """,
                    (now, fingerprint),
                )
            return claim_id

        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO bean_claims(
                    ts, claim_type, subject, statement, confidence, source,
                    evidence_json, horizon_seconds
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    now,
                    kind,
                    claim.subject,
                    claim.statement,
                    max(0.0, min(1.0, float(claim.confidence))),
                    claim.source,
                    json.dumps(list(claim.evidence), separators=(",", ":")),
                    claim.horizon_seconds,
                ),
            )
            claim_id = int(cur.lastrowid)
            self.conn.execute(
                """
                INSERT INTO bean_claim_rollups(
                    fingerprint,claim_id,first_seen,last_seen,occurrences
                ) VALUES(?,?,?,?,1)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    claim_id=excluded.claim_id,
                    first_seen=excluded.first_seen,
                    last_seen=excluded.last_seen,
                    occurrences=1
                """,
                (fingerprint, claim_id, now, now),
            )
        return claim_id

    def record_research(self, research: dict[str, Any]) -> None:
        if not research.get("available"):
            return

        eth = research.get("eth") or {}
        human = research.get("human") or {}
        technical = float(research.get("technical_score", research.get("composite_score", 0.0)))
        human_score = float(research.get("human_score", 0.0))
        composite = float(research.get("composite_score", 0.0))
        confidence = float(research.get("confidence", 0.0))
        price = float(eth.get("price") or 0.0)

        news_signal = float(human.get("news_sentiment", 0.0))
        social_signal = float(human.get("social_sentiment", 0.0))
        politics_signal = float(human.get("politics_sentiment", 0.0))
        crowd_signal = float(research.get("crowd_edge", 0.0))
        fear_greed_signal = 0.0
        if human.get("fear_greed_value") is not None:
            fear_greed_signal = max(
                -1.0,
                min(1.0, (float(human.get("fear_greed_value", 50.0)) - 50.0) / 50.0),
            )

        self.add_claim(
            BeanClaim(
                "OBSERVATION",
                "ETH",
                (
                    f"ETH price {price:.8f}; 5m {float(eth.get('return_5m', 0)):+.4%}; "
                    f"1h {float(eth.get('return_1h', 0)):+.4%}; 6h {float(eth.get('return_6h', 0)):+.4%}."
                ),
                1.0,
                "coinbase_market",
                ("price", "returns", "volume", "spread"),
            )
        )

        if human.get("available"):
            self.add_claim(
                BeanClaim(
                    "OBSERVATION",
                    "CROWD",
                    (
                        f"FOMO {float(human.get('fomo_index', 0)):.1f}/100; "
                        f"regime {human.get('crowd_regime', 'UNKNOWN')}; "
                        f"news {float(human.get('news_sentiment', 0)):+.3f}; "
                        f"social {float(human.get('social_sentiment', 0)):+.3f}; "
                        f"politics {float(human.get('politics_sentiment', 0)):+.3f}."
                    ),
                    0.8,
                    "human_weather",
                    ("gdelt", "reddit", "fear_greed"),
                )
            )

        self.add_claim(
            BeanClaim(
                "INFERENCE",
                "ETH",
                f"Technical score {technical:+.3f}; human score {human_score:+.3f}; composite {composite:+.3f}.",
                confidence,
                "research_fusion",
                ("coinbase_market", "human_weather"),
            )
        )

        if abs(technical) >= 0.10 and abs(human_score) >= 0.10 and technical * human_score < 0:
            severity = min(1.0, (abs(technical) + abs(human_score)) / 2.0)
            self.record_contradiction(
                subject="ETH",
                left_source="technical",
                left_value=technical,
                right_source="human",
                right_value=human_score,
                severity=severity,
                note="Technical and human evidence disagree on direction.",
            )

        if price > 0 and abs(composite) >= 0.08:
            direction = 1 if composite > 0 else -1
            for horizon in (300, 3600, 21600):
                if not self._recent_duplicate("ETH", horizon, direction, within_seconds=120):
                    self.add_prediction(
                        subject="ETH",
                        horizon_seconds=horizon,
                        start_price=price,
                        predicted_direction=direction,
                        predicted_strength=abs(composite),
                        confidence=confidence,
                        source_mix={
                            "technical": technical,
                            "human": human_score,
                            "news": news_signal,
                            "social": social_signal,
                            "politics": politics_signal,
                            "crowd": crowd_signal,
                            "fear_greed": fear_greed_signal,
                            "composite": composite,
                        },
                    )

    def record_universe(self, universe: dict[str, Any]) -> None:
        """Record high-signal Coinbase-wide assets without pretending they are executable."""
        for item in (universe.get("top") or [])[:12]:
            product = str(item.get("product") or "")
            base = str(item.get("base") or product.split("-", 1)[0] or "")
            price = float(item.get("price") or 0.0)
            score = float(item.get("score") or 0.0)
            if not base or price <= 0:
                continue

            self.add_claim(
                BeanClaim(
                    "OBSERVATION",
                    base,
                    (
                        f"{product} price {price:.8f}; 5m {float(item.get('return_5m', 0)):+.4%}; "
                        f"1h {float(item.get('return_1h', 0)):+.4%}; "
                        f"6h {float(item.get('return_6h', 0)):+.4%}; "
                        f"volume {float(item.get('volume_ratio', 0)):.2f}x."
                    ),
                    1.0,
                    "coinbase_universe",
                    (product, "price", "returns", "volume", "spread"),
                )
            )

            if abs(score) < 0.12:
                continue
            direction = 1 if score > 0 else -1
            if not self._recent_duplicate(base, 3600, direction, within_seconds=300):
                self.add_prediction(
                    subject=base,
                    horizon_seconds=3600,
                    start_price=price,
                    predicted_direction=direction,
                    predicted_strength=abs(score),
                    confidence=min(0.85, 0.35 + abs(score) * 0.5),
                    source_mix={
                        "coinbase_universe": score,
                    },
                )

    def add_prediction(
        self,
        subject: str,
        horizon_seconds: int,
        start_price: float,
        predicted_direction: int,
        predicted_strength: float,
        confidence: float,
        source_mix: dict[str, float],
    ) -> int:
        now = time.time()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO bean_predictions(
                    created_at, due_at, subject, horizon_seconds, start_price,
                    predicted_direction, predicted_strength, confidence, source_mix_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    now,
                    now + int(horizon_seconds),
                    subject,
                    int(horizon_seconds),
                    float(start_price),
                    1 if predicted_direction >= 0 else -1,
                    max(0.0, min(1.0, float(predicted_strength))),
                    max(0.0, min(1.0, float(confidence))),
                    json.dumps(source_mix, separators=(",", ":")),
                ),
            )
        return int(cur.lastrowid)

    def _recent_duplicate(
        self,
        subject: str,
        horizon_seconds: int,
        direction: int,
        within_seconds: float,
    ) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM bean_predictions
            WHERE subject=? AND horizon_seconds=? AND predicted_direction=?
              AND created_at >= ?
            LIMIT 1
            """,
            (subject, int(horizon_seconds), 1 if direction >= 0 else -1, time.time() - within_seconds),
        ).fetchone()
        return row is not None

    def record_contradiction(
        self,
        subject: str,
        left_source: str,
        left_value: float,
        right_source: str,
        right_value: float,
        severity: float,
        note: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO bean_contradictions(
                    ts, subject, left_source, left_value, right_source,
                    right_value, severity, note
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    time.time(),
                    subject,
                    left_source,
                    float(left_value),
                    right_source,
                    float(right_value),
                    max(0.0, min(1.0, float(severity))),
                    note,
                ),
            )

    def resolve_due(self, prices: dict[str, float]) -> int:
        now = time.time()
        rows = self.conn.execute(
            """
            SELECT * FROM bean_predictions
            WHERE resolved_at IS NULL AND due_at <= ?
            ORDER BY id
            """,
            (now,),
        ).fetchall()
        resolved = 0
        for row in rows:
            subject = str(row["subject"])
            end_price = float(prices.get(subject, 0.0))
            start_price = float(row["start_price"])
            if end_price <= 0 or start_price <= 0:
                continue

            actual_return = (end_price / start_price) - 1.0
            actual_direction = 1 if actual_return > 0 else -1 if actual_return < 0 else 0
            predicted_direction = int(row["predicted_direction"])
            hit = 1 if actual_direction == predicted_direction else 0
            magnitude = min(1.0, abs(actual_return) / 0.03)
            confidence = float(row["confidence"])
            score = (1.0 if hit else -1.0) * (0.35 + 0.65 * magnitude) * (0.5 + 0.5 * confidence)

            with self.conn:
                self.conn.execute(
                    """
                    UPDATE bean_predictions
                    SET resolved_at=?, end_price=?, actual_return=?, hit=?, score=?
                    WHERE id=?
                    """,
                    (now, end_price, actual_return, hit, score, int(row["id"])),
                )

            mix = json.loads(str(row["source_mix_json"]) or "{}")
            for source, value in mix.items():
                if abs(float(value)) < 0.05:
                    continue
                source_direction = 1 if float(value) > 0 else -1
                source_hit = 1 if actual_direction == source_direction else 0
                source_score = (1.0 if source_hit else -1.0) * (0.35 + 0.65 * magnitude)
                self._update_source(source, int(row["horizon_seconds"]), source_hit, source_score)

            resolved += 1
        return resolved

    def _update_source(self, source: str, horizon: int, hit: int, score: float) -> None:
        row = self.conn.execute(
            "SELECT * FROM bean_source_stats WHERE source=? AND horizon_seconds=?",
            (source, horizon),
        ).fetchone()
        samples = int(row["samples"]) if row else 0
        hits = int(row["hits"]) if row else 0
        score_sum = float(row["score_sum"]) if row else 0.0
        samples += 1
        hits += int(hit)
        score_sum += float(score)
        hit_rate = hits / samples
        mean_score = score_sum / samples
        # Bayesian shrinkage keeps tiny samples from becoming overconfident.
        shrunk_hit = (hits + 3.0) / (samples + 6.0)
        trust = max(0.05, min(0.95, 0.65 * shrunk_hit + 0.35 * (0.5 + 0.5 * math.tanh(mean_score))))

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO bean_source_stats(source,horizon_seconds,samples,hits,score_sum,trust)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(source,horizon_seconds) DO UPDATE SET
                    samples=excluded.samples,
                    hits=excluded.hits,
                    score_sum=excluded.score_sum,
                    trust=excluded.trust
                """,
                (source, horizon, samples, hits, score_sum, trust),
            )

    def trust(self, source: str, horizon_seconds: int = 3600) -> float:
        row = self.conn.execute(
            "SELECT trust FROM bean_source_stats WHERE source=? AND horizon_seconds=?",
            (source, int(horizon_seconds)),
        ).fetchone()
        return float(row["trust"]) if row else 0.5

    def snapshot(self) -> dict[str, Any]:
        pending = int(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM bean_predictions WHERE resolved_at IS NULL"
            ).fetchone()["n"]
        )
        resolved = int(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM bean_predictions WHERE resolved_at IS NOT NULL"
            ).fetchone()["n"]
        )
        claims = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM bean_claims").fetchone()["n"]
        )
        contradictions = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM bean_contradictions").fetchone()["n"]
        )
        rolled_up = int(
            self.conn.execute(
                "SELECT COALESCE(SUM(occurrences - 1),0) AS n FROM bean_claim_rollups"
            ).fetchone()["n"]
        )
        stats = self.conn.execute(
            """
            SELECT source,horizon_seconds,samples,hits,trust
            FROM bean_source_stats
            ORDER BY samples DESC, trust DESC
            LIMIT 12
            """
        ).fetchall()
        latest = self.conn.execute(
            """
            SELECT subject,horizon_seconds,predicted_direction,predicted_strength,
                   confidence,due_at
            FROM bean_predictions
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        return {
            "claims": claims,
            "pending_predictions": pending,
            "resolved_predictions": resolved,
            "contradictions": contradictions,
            "compressed_repeats": rolled_up,
            "trust": [dict(row) for row in stats],
            "latest_prediction": dict(latest) if latest else None,
        }
