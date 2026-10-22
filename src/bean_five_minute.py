"""Causal five-minute Coinbase research data and forward-only paper execution.

No wallet imports, keys, order routing or exchange order submission. Backtests
use hourly School's genetic machinery, but on a DISTINCT 300-second dataset;
forward accounting only consumes candles first observed after trial creation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, quote

from .bean_strategy_lab import (
    BASELINE, CAPITAL, COST, HOUR, _choose, _get_json,
    discover_products, select_markets,
)
from .core import app_data_dir

BAR = 300
BARS = 288
PAPER_MIN_SAMPLES = 288      # at least 24 hours of genuinely forward 5m candles
MIN_TRADE_COUNT = 2
MIN_EDGE = 0.02
MAX_DRAWDOWN = 0.10


def fetch_5m(product, start, end):
    query = urlencode({
        "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "granularity": BAR,
    })
    url = ("https://api.exchange.coinbase.com/products/" +
           quote(product, safe="-") + "/candles?" + query)
    candles = _get_json(url)
    if not isinstance(candles, list):
        raise ValueError("Coinbase five-minute candles unavailable")
    out = {}
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        ts, op, cl = int(row[0]), float(row[3]), float(row[4])
        try:
            volume = float(row[5])
        except (TypeError, ValueError, OverflowError):
            volume = float("nan")
        if (start <= ts < end and ts % BAR == 0 and
                math.isfinite(op) and math.isfinite(cl) and op > 0 and cl > 0):
            candle = {"open": op, "close": cl}
            # Retain authentic Coinbase base-asset volume for research.
            # Invalid observations stay missing, never synthetic.
            if math.isfinite(volume) and volume >= 0:
                candle["volume"] = volume
            out[ts] = candle
    return out


def collect_5m(index, *, catalog=None, fetcher=None, end=None):
    """Actual 24h five-minute close/open history, never filled with fake bars."""
    catalog = discover_products() if catalog is None else list(catalog)
    fetcher = fetch_5m if fetcher is None else fetcher
    end = int(time.time() // BAR * BAR) if end is None else int(end)
    chosen = select_markets(catalog, index, extra=3)
    start = end - BARS * BAR
    histories, skipped = {}, {}
    for product in chosen:
        try:
            data = fetcher(product, start, end)
            if len(data) < 260:
                skipped[product] = f"Only {len(data)} of 288 five-minute candles available"
            else:
                histories[product] = data
        except Exception as exc:
            skipped[product] = f"{type(exc).__name__}: {str(exc)[:150]}"
    if "BTC-USD" not in histories:
        raise ValueError("BTC-USD 5m benchmark unavailable: " +
                         skipped.get("BTC-USD", "not listed"))
    btc_times = sorted(histories["BTC-USD"])
    # Require exactly contiguous recent candles; don't silently bridge outages.
    streak = [btc_times[-1]]
    while streak[-1] - BAR in histories["BTC-USD"]:
        streak.append(streak[-1] - BAR)
    times = sorted(streak)
    if len(times) < 256 or times[-1] != end-BAR:
        raise ValueError("Not enough contiguous fresh BTC 5m candles")
    pairs = [name for name, prices in histories.items()
             if all(t in prices for t in times)]
    for name in histories:
        if name not in pairs:
            skipped[name] = "Incomplete matching 5m history"
    bars = [{"ts": t, "prices": {name: histories[name][t] for name in pairs}}
            for t in times]
    return {"bars": bars, "pairs": pairs, "attempted": chosen,
            "skipped": skipped, "catalog_count": len(catalog), "end": end,
            "granularity_seconds": BAR,
            "source": "Real Coinbase Exchange 300-second candles; research backtest only"}


class FastPaperBook:
    """One independent paper candidate held fixed until 24h forward verdict.

    Every BUY/SELL is at the OPEN of the *next* completed candle after the
    recorded signal. This is hypothetical execution with modeled costs, not a
    Coinbase exchange fill. Never accesses wallet balances or submits orders.
    """
    def __init__(self, *, path=None, fetcher=None, clock=None):
        self.path = Path(path) if path else app_data_dir() / "bean-five-minute-paper.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or fetch_5m
        self.clock = clock or time.time
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS paper (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL)""")

    from contextlib import contextmanager
    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, selected):
        """Enqueue a new strategy only if no independent paper test is active."""
        if not selected or selected.get("state") != "AUTO_SELECTED":
            return False
        report = selected.get("report") or {}
        candidate = report.get("candidate")
        pairs = report.get("pairs")
        if (not isinstance(candidate, list) or len(candidate) != 4 or
                not isinstance(pairs, list) or "BTC-USD" not in pairs or
                not 1 <= len(pairs) <= 12):
            return False
        key = str(selected["id"])
        now = float(self.clock())
        data = {"candidate": candidate, "pairs": pairs,
                "experiment": selected.get("ordinal"), "last_bar": None,
                "pending": None, "held": None, "units": 0.0,
                "cash": CAPITAL, "equity": CAPITAL, "peak": CAPITAL,
                "drawdown": 0.0, "orders": 0, "samples": 0,
                "first_btc": None, "last_target": None,
                "last_error": None, "net_return": 0.0, "btc_return": None,
                "source": "Forward paper, next-bar open fills, modeled costs"}
        with self._lock, self._connect() as db:
            if db.execute("SELECT 1 FROM paper WHERE state='ACTIVE' LIMIT 1").fetchone():
                return False
            if db.execute("SELECT 1 FROM paper WHERE id=?", (key,)).fetchone():
                return False
            db.execute("INSERT INTO paper(id,state,data,created,updated) VALUES(?,?,?,?,?)",
                       (key, "ACTIVE", json.dumps(data), now, now))
        return True

    def step(self, *, end=None):
        end = int(self.clock() // BAR * BAR) if end is None else int(end)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM paper WHERE state='ACTIVE' "
                             "ORDER BY created ASC LIMIT 1").fetchone()
            if row is None:
                return {"status": "NO_ACTIVE_FORWARD_TRIAL"}
            data = json.loads(row["data"])
            last = end-BAR
            if data["last_bar"] is not None and last <= data["last_bar"]:
                return {"status": "WAITING", **data}
            # A skipped observation cannot be backfilled into a pretend
            # real-time trade. Mark the gap and keep the paper position.
            if data["last_bar"] is not None and last-data["last_bar"] != BAR:
                data["pending"] = None
                data["last_bar"] = last
                data["last_error"] = "Missing forward candle interval; no paper fill credited"
                db.execute("UPDATE paper SET data=?,updated=? WHERE id=?",
                           (json.dumps(data), self.clock(), row["id"]))
                return {"status": "DATA_GAP", **data}
            lookback = 75
            start = end - lookback*BAR
            histories = {}
            try:
                for p in data["pairs"]:
                    histories[p] = self.fetcher(p, start, end)
                ts = list(range(start, end, BAR))
                if any(t not in histories[p] for p in data["pairs"] for t in ts):
                    raise ValueError("Missing matching 5m price histories")
                bars = [{"ts": t, "prices": {p: histories[p][t] for p in data["pairs"]}}
                        for t in ts]
                candle = bars[-1]["prices"]
                if data["first_btc"] is None:
                    data["first_btc"] = candle["BTC-USD"]["close"]
                # Previous close's target can fill at this candle's opening,
                # never at a previously observed bar's price.
                pending = data["pending"]
                if pending != data["held"] and data["last_bar"] is not None:
                    if data["held"] is not None:
                        data["cash"] += data["units"]*candle[data["held"]]["open"]*(1-COST)
                        data["orders"] += 1
                        data["held"], data["units"] = None, 0.0
                    if pending is not None and data["cash"] >= 2.0:
                        data["units"] = data["cash"]*(1-COST)/candle[pending]["open"]
                        data["cash"], data["held"] = 0.0, pending
                        data["orders"] += 1
                held = data["held"]
                equity = (data["cash"] + data["units"]*candle[held]["close"]*(1-COST)
                          if held else data["cash"])
                data["peak"] = max(data["peak"], equity)
                data["drawdown"] = max(data["drawdown"], 1-equity/data["peak"])
                data["equity"] = equity
                data["net_return"] = round(equity/CAPITAL-1, 6)
                btc_close = candle["BTC-USD"]["close"]
                data["btc_return"] = round(
                    btc_close/data["first_btc"]*(1-COST)**2-1, 6)
                data["pending"] = _choose(bars, len(bars), data["pairs"], data["candidate"])
                data["last_target"] = data["pending"]
                data["last_bar"] = last
                data["last_error"] = None
                data["samples"] += 1
            except Exception as exc:
                data["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                return {"status": "DATA_UNAVAILABLE", **data}
            status = "ACTIVE"
            if data["samples"] >= PAPER_MIN_SAMPLES:
                passed = (data["orders"] >= MIN_TRADE_COUNT and
                          data["net_return"] >= max(0.0, data["btc_return"]+MIN_EDGE) and
                          data["drawdown"] <= MAX_DRAWDOWN)
                status = "VALIDATED_FORWARD_PAPER" if passed else "REJECTED_FORWARD_PAPER"
            db.execute("UPDATE paper SET state=?,data=?,updated=? WHERE id=?",
                       (status, json.dumps(data), self.clock(), row["id"]))
            return {"status": status, **data}

    def status(self):
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM paper ORDER BY "
                             "CASE state WHEN 'ACTIVE' THEN 0 ELSE 1 END,"
                             "updated DESC LIMIT 1").fetchone()
        return {"status": row["state"], **json.loads(row["data"])} if row else {
            "status": "NO_ACTIVE_FORWARD_TRIAL"}
