"""BEAN Strategy Lab: autonomous research, no user approval gate.

Never imports wallet rails or places orders. Every event preserves training,
holdout and lessons in local SQLite. Qualified trials auto-select with no
human approval. Strategy deployment belongs to the autonomous code engineer.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from uuid import uuid4

from .core import app_data_dir

HOUR = 3600
HOURS = 288       # 12 days, within Coinbase's 300-candle request limit
WARMUP = 60       # identical initial observation window for all candidates
COST = 0.006      # research fee + adverse fill/slippage per side
CAPITAL = 25.0
BASELINE = ("BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "SHIB-USD")
MODES = ("momentum", "reversal", "dual_trend", "breakout", "persistent")
LOOKBACKS = (3, 6, 12, 24, 48)
THRESHOLDS = (0.0, 0.003, 0.008, 0.016, 0.03)
REBALANCES = (3, 6, 12, 24)
GENERATION_COUNT = 5
POPULATION = 28


def _get_json(url: str):
    request = Request(url, headers={"User-Agent": "AIscend-BEAN-StrategyLab/1",
                                    "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(min(10, 2 ** (attempt + 1)))
    raise RuntimeError("Coinbase did not return market data")


def discover_products():
    """Research the full currently-online dollar-quote catalog, never a whitelist."""
    data = _get_json("https://api.exchange.coinbase.com/products")
    if not isinstance(data, list):
        raise ValueError("Coinbase catalog response was not a list")
    by_base = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        base = str(item.get("base_currency") or "").upper()
        q = str(item.get("quote_currency") or "").upper()
        pid = str(item.get("id") or "").upper()
        if (item.get("status") != "online" or item.get("trading_disabled")
                or q not in {"USD", "USDC"} or base in {"USD", "USDC"} or not base):
            continue
        old = by_base.get(base)
        if old is None or (q == "USD" and old.endswith("-USDC")):
            by_base[base] = pid
    return sorted(by_base.values())


def select_markets(catalog: list[str], event_index: int, extra: int = 5):
    """Core majors every exam; rotate remaining Coinbase markets on Retry."""
    chosen = [p for p in BASELINE if p in catalog]
    others = [p for p in catalog if p not in chosen]
    if others:
        offset = (event_index * extra) % len(others)
        chosen.extend(others[(offset + i) % len(others)] for i in range(min(extra, len(others))))
    return list(dict.fromkeys(chosen))


def fetch_candles(product: str, start: int, end: int):
    query = urlencode({"start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                       "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
                       "granularity": HOUR})
    url = f"https://api.exchange.coinbase.com/products/{quote(product, safe='-')}/candles?{query}"
    payload = _get_json(url)
    if not isinstance(payload, list):
        raise ValueError("Coinbase candles were not a list")
    out = {}
    for candle in payload:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            continue
        ts, op, cl = int(candle[0]), float(candle[3]), float(candle[4])
        if (start <= ts < end and ts % HOUR == 0 and
                math.isfinite(op) and math.isfinite(cl) and op > 0 and cl > 0):
            out[ts] = {"open": op, "close": cl}
    return out


def _longest_contiguous(timestamps):
    longest, current = [], []
    for ts in sorted(timestamps):
        if current and ts != current[-1] + HOUR:
            if len(current) > len(longest):
                longest = current
            current = []
        current.append(ts)
    return current if len(current) > len(longest) else longest


def collect(event_index, *, catalog=None, fetcher=None, end=None):
    catalog = discover_products() if catalog is None else list(catalog)
    fetcher = fetch_candles if fetcher is None else fetcher
    if end is None:
        end = int(time.time() // HOUR * HOUR)
    start = end - HOURS * HOUR
    chosen = select_markets(catalog, event_index)
    collected, failures = {}, {}
    for product in chosen:
        try:
            history = fetcher(product, start, end)
            if len(history) < 240:
                failures[product] = f"Only {len(history)} complete hourly candles"
            else:
                collected[product] = history
        except Exception as exc:
            failures[product] = f"{type(exc).__name__}: {str(exc)[:160]}"
    if "BTC-USD" not in collected:
        raise ValueError("BTC-USD historical benchmark unavailable: " +
                         failures.get("BTC-USD", "not listed"))
    times = _longest_contiguous(collected["BTC-USD"])
    if len(times) < 220:
        raise ValueError("Insufficient contiguous BTC price observations")
    pairs = []
    for name, prices in collected.items():
        if all(ts in prices for ts in times):
            pairs.append(name)
        else:
            failures[name] = "History has missing hours; not substituted with synthetic data"
    if not pairs or pairs[0] != "BTC-USD":
        raise ValueError("No reliable BTC benchmark")
    bars = [{"ts": ts, "prices": {p: collected[p][ts] for p in pairs}} for ts in times]
    return {"bars": bars, "pairs": pairs, "attempted": chosen,
            "skipped": failures, "catalog_count": len(catalog), "end": end,
            "source": "Coinbase public exchange candles (actual hourly prices)"}


def _choose(bars, i, pairs, rule):
    mode, lookback, threshold, _ = rule
    recent, past = bars[i-1]["prices"], bars[i-lookback-1]["prices"]
    scores = {p: recent[p]["close"] / past[p]["close"] - 1 for p in pairs}
    if mode == "reversal":
        eligible = {p: -v for p, v in scores.items() if v < -threshold}
    elif mode == "dual_trend":
        back = bars[i-49]["prices"]
        eligible = {p: v for p, v in scores.items()
                    if v > threshold and recent[p]["close"] > back[p]["close"]}
    elif mode == "breakout":
        eligible = {p: v for p, v in scores.items()
                    if v > threshold and recent[p]["close"] >=
                    max(bars[k]["prices"][p]["close"] for k in range(i-lookback-1, i-1))}
    else:
        eligible = {p: v for p, v in scores.items() if v > threshold}
    return max(eligible, key=eligible.get) if eligible else None


def simulate(bars, pairs, rule):
    """Trade strictly at next bar open; decisions depend only on past closes."""
    if len(bars) <= WARMUP + 4:
        raise ValueError("Not enough hourly bars")
    cash, held, units, trades = CAPITAL, None, 0.0, 0
    peak, drawdown = CAPITAL, 0.0
    for i in range(WARMUP, len(bars)):
        today = bars[i]["prices"]
        _, _, threshold, rebalance = rule
        if (i - WARMUP) % rebalance == 0:
            proposed = _choose(bars, i, pairs, rule)
            if rule[0] == "persistent" and held is not None:
                previous = bars[i-1]["prices"][held]["close"]
                old = bars[i-rule[1]-1]["prices"][held]["close"]
                if previous / old - 1 >= -threshold:
                    proposed = held
            if proposed != held:
                if held is not None:
                    cash += units * today[held]["open"] * (1-COST)
                    trades += 1
                    held, units = None, 0.0
                if proposed is not None and cash >= 2.0:
                    units = cash * (1-COST) / today[proposed]["open"]
                    cash, held = 0.0, proposed
                    trades += 1
        equity = cash + (units * today[held]["close"] * (1-COST) if held else 0.0)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1-equity/peak)
    final = cash + (units * bars[-1]["prices"][held]["close"] * (1-COST) if held else 0.0)
    return {"final": round(final, 6), "net_return": round(final/CAPITAL-1, 6),
            "drawdown": round(drawdown, 6), "orders": trades}


def _random(rng):
    return (rng.choice(MODES), rng.choice(LOOKBACKS),
            rng.choice(THRESHOLDS), rng.choice(REBALANCES))


def _mutate(g, rng):
    fields = list(g)
    index = rng.randrange(4)
    fields[index] = rng.choice((MODES, LOOKBACKS, THRESHOLDS, REBALANCES)[index])
    return tuple(fields)


def _fitness(bars, pairs, rule):
    # Candidate selection uses training-only windows. Validation isn't read.
    a = simulate(bars, pairs, rule)
    b = simulate(bars[len(bars)//5:], pairs, rule)
    return min(a["net_return"], b["net_return"]) - 0.6 * max(a["drawdown"], b["drawdown"])


def evolve(dataset, *, seed, generations=GENERATION_COUNT, population=POPULATION, memory=None):
    bars, pairs = dataset["bars"], dataset["pairs"]
    cut = int(len(bars) * 0.68)
    train, holdout = bars[:cut], bars[cut:]
    if len(train) <= WARMUP + 8 or len(holdout) <= WARMUP + 4:
        raise ValueError("Not enough independent training and evaluation data")
    rng = random.Random(seed)
    prior = []
    for candidate in (memory or []):
        entry = tuple(candidate)
        if (len(entry) == 4 and entry[0] in MODES and entry[1] in LOOKBACKS
                and entry[2] in THRESHOLDS and entry[3] in REBALANCES
                and entry not in prior):
            prior.append(entry)
    # Retest previous training champions on the NEW training period. Past
    # holdout outcomes are deliberately never used as optimization scores.
    genes = prior[:population] + [_random(rng) for _ in range(max(0, population-len(prior)))]
    grades, history = {}, []
    def fitness(g):
        if g not in grades:
            grades[g] = _fitness(train, pairs, g)
        return grades[g]
    for gen in range(generations):
        ranked = sorted(set(genes), key=lambda g: (-fitness(g), g))
        parents = ranked[:max(3, population // 4)]
        history.append({"generation": gen+1, "unique_tested": len(grades),
                        "best_training_score": round(fitness(parents[0]), 6),
                        "leader": list(parents[0])})
        genes = parents[:]
        while len(genes) < population:
            r = rng.random()
            if r < .2:
                genes.append(_random(rng))
            elif r < .7:
                genes.append(_mutate(rng.choice(parents), rng))
            else:
                a, b = rng.sample(parents, 2)
                genes.append(_mutate(tuple(rng.choice((x, y)) for x, y in zip(a, b)), rng))
    champion = max(grades, key=lambda g: (grades[g], g))
    result = simulate(holdout, pairs, champion)
    # Same observation offset and fee model for buy-and-hold BTC benchmark.
    btc = "BTC-USD"
    btc_final = (CAPITAL * (1-COST)**2 *
                 holdout[-1]["prices"][btc]["close"] /
                 holdout[WARMUP]["prices"][btc]["open"])
    btc_return = btc_final / CAPITAL - 1
    return {"schema": "bean.manual.school.v1", "candidate": list(champion),
            "train_score": round(grades[champion], 6), "generations": history,
            "unique_candidates": len(grades), "memory_seeds_tested": len(prior[:population]),
            "holdout": result,
            "btc_return": round(btc_return, 6),
            "profitable": result["net_return"] > 0,
            "beats_cash_and_btc": result["net_return"] > max(0, btc_return),
            "source": dataset["source"], "pairs": pairs,
            "catalog_count": dataset["catalog_count"],
            "attempted": dataset["attempted"], "skipped": dataset["skipped"],
            "data_end": dataset["end"],
            "lessons": [
                "PASS on the current historical holdout; still requires genuinely new forward data."
                if result["net_return"] > max(0, btc_return)
                else "This candidate failed to beat cash and BTC after modeled costs; retain the training rules but do not promote.",
                "Experimented on %d of %d selected markets; missing history is not filled in." %
                (len(pairs), len(dataset["attempted"])),
            ],
            "limitations": "Research-only. Historical prices with modeled costs, no real fills. "
                           "Repeated runs on similar periods are not independent forward validation."}


class BeanStrategyLab:
    def __init__(self, path=None, *, collector=None):
        self.path = Path(path) if path is not None else app_data_dir() / "bean-strategy-lab.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.collector = collector or collect
        self._lock = threading.RLock()
        self._worker = None
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY, created REAL NOT NULL, finished REAL,
                state TEXT NOT NULL, ordinal INTEGER NOT NULL, seed INTEGER NOT NULL,
                result TEXT, error TEXT, approved INTEGER NOT NULL DEFAULT 0)""")
            # Recover interrupted runs after app shutdown without erasing lessons.
            db.execute("UPDATE experiments SET state='INTERRUPTED' WHERE state='RUNNING' AND created < ?", (time.time()-1200,))

    @contextmanager
    def _connect(self):
        # sqlite3's built-in context manager commits but does NOT close files.
        # Closing explicitly matters on Windows, including test cleanup.
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _valid_lesson(candidate):
        """Only replay known bounded strategy genes, never a saved holdout grade."""
        if not isinstance(candidate, (list, tuple)) or len(candidate) != 4:
            return None
        mode, lookback, threshold, rebalance = candidate
        if (mode in MODES and type(lookback) is int and lookback in LOOKBACKS
                and type(threshold) in (int, float) and threshold in THRESHOLDS
                and type(rebalance) is int and rebalance in REBALANCES):
            return (mode, lookback, threshold, rebalance)
        return None

    def _lesson_archive(self, db, *, exclude_id=None):
        """Recover every distinct prior candidate from the durable SQLite journal.

        Research replays parameters only; historical holdout rewards do not enter
        future training. Old lessons remain eligible beyond the last eight runs.
        """
        rows = db.execute(
            "SELECT result FROM experiments WHERE result IS NOT NULL "
            "AND (? IS NULL OR id != ?) ORDER BY ordinal DESC",
            (exclude_id, exclude_id),
        ).fetchall()
        seen, archive = set(), []
        for row in rows:
            try:
                report = json.loads(row["result"])
                candidate = self._valid_lesson(report.get("candidate"))
            except (TypeError, ValueError, AttributeError):
                continue
            if candidate is not None and candidate not in seen:
                seen.add(candidate)
                archive.append(candidate)
        return archive

    @staticmethod
    def _replay_lessons(archive, ordinal):
        """Replay recent genes plus a rotating slice of the full older archive."""
        recent, older = list(archive[:8]), archive[8:]
        if older:
            offset = (max(1, ordinal) - 1) * 8 % len(older)
            recent.extend(older[(offset + i) % len(older)]
                          for i in range(min(8, len(older))))
        return recent

    def status(self):
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM experiments ORDER BY created DESC LIMIT 12").fetchall()
            total = db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
            archived = len(self._lesson_archive(db))
            selected = db.execute("SELECT COUNT(*) FROM experiments "
                                  "WHERE state='AUTO_SELECTED'").fetchone()[0]
        history = []
        for row in rows:
            report = json.loads(row["result"]) if row["result"] else None
            history.append({"id": row["id"], "state": row["state"],
                            "ordinal": row["ordinal"], "created": row["created"],
                            "error": row["error"], "approved": bool(row["approved"]),
                            "report": report})
        return {"running": any(x["state"] == "RUNNING" for x in history),
                "latest": history[0] if history else None,
                "history": history, "retained_events": total,
                "retained_lessons": archived, "research_selected": selected,
                "live_strategy_activation": "NOT_DEPLOYED",
                "acceptance": "Selected historical candidates are research only. Prior candidate genes are replayed from the complete journal; no Strategy School policy is applied to live orders."}

    def start(self):
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("BEAN is already running an experiment")
            with self._connect() as db:
                active = db.execute("SELECT id FROM experiments WHERE state='RUNNING' LIMIT 1").fetchone()
                if active:
                    raise RuntimeError("Another BEAN experiment is already running")
                ordinal = db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] + 1
                event_id = str(uuid4())
                seed = 6410 + ordinal
                db.execute("INSERT INTO experiments(id,created,state,ordinal,seed) VALUES(?,?,?,?,?)",
                           (event_id, time.time(), "RUNNING", ordinal, seed))
            self._worker = threading.Thread(target=self._work, args=(event_id, ordinal, seed),
                                             name="bean-strategy-lab", daemon=True)
            self._worker.start()
        return self.status()

    def _work(self, event_id, ordinal, seed):
        try:
            dataset = self.collector(ordinal-1)
            with self._lock, self._connect() as db:
                archive = self._lesson_archive(db, exclude_id=event_id)
            # All past parameters survive, including rejected experiments.
            # Replay recent genes and rotate older ones; never use prior holdout
            # profitability to choose training seeds or to authorize trades.
            inherited = self._replay_lessons(archive, ordinal)
            report = evolve(dataset, seed=seed, memory=inherited)
            report["memory_available"] = len(archive)
            report["memory_replayed"] = len(inherited)
            report["live_strategy_activation"] = "NOT_DEPLOYED"
            result = json.dumps(report, separators=(",", ":"))
            state, error = ("AUTO_SELECTED" if report.get("beats_cash_and_btc")
                            else "REJECTED_BY_EVIDENCE"), None
        except Exception as exc:
            result, state, error = None, "FAILED", f"{type(exc).__name__}: {str(exc)[:260]}"
        with self._lock, self._connect() as db:
            db.execute("UPDATE experiments SET state=?, finished=?, result=?, error=?, approved=? WHERE id=?",
                       (state, time.time(), result, error, int(state == "AUTO_SELECTED"), event_id))

    def approve(self, event_id):
        """Legacy API compatibility; no human approval is required or consumed."""
        with self._lock, self._connect() as db:
            row = db.execute("SELECT state FROM experiments WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown BEAN experiment")
            if row["state"] != "AUTO_SELECTED":
                raise ValueError("Candidate did not pass autonomous evidence gates")
        return self.status()
