"""Causal five-minute Coinbase research data and forward-only paper execution.

No wallet imports, keys, order routing or exchange order submission. Backtests
use hourly School's genetic machinery, but on a DISTINCT 300-second dataset;
forward accounting only consumes candles first observed after trial creation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, quote

from . import bean_strategy_lab as strategy_lab
from .bean_strategy_lab import (
    BASELINE, CAPITAL, COST, HOUR, MODES, LOOKBACKS, THRESHOLDS,
    REBALANCES, _choose, _get_json, discover_products, select_markets,
)
from .core import app_data_dir

BAR = 300
BARS = 288
PAPER_MIN_SAMPLES = 288      # at least 24 hours of genuinely forward 5m candles
MIN_TRADE_COUNT = 2
MIN_EDGE = 0.02
MAX_DRAWDOWN = 0.10


def _paper_source_sha():
    """Pin a forward trial to its actual paper and strategy implementation.

    A source change during a trial must not silently combine old and new
    trading rules into one supposedly prospective performance record.
    """
    digest = hashlib.sha256()
    try:
        for source in (Path(__file__), Path(strategy_lab.__file__)):
            payload = source.read_bytes()
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    except OSError as exc:
        raise RuntimeError("Cannot fingerprint forward paper source") from exc
    return digest.hexdigest()


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
                candidate[0] not in MODES or
                type(candidate[1]) is not int or candidate[1] not in LOOKBACKS or
                type(candidate[2]) not in (int, float) or
                candidate[2] not in THRESHOLDS or
                type(candidate[3]) is not int or candidate[3] not in REBALANCES or
                not isinstance(pairs, list) or "BTC-USD" not in pairs or
                not 1 <= len(pairs) <= 12):
            return False
        key = str(selected["id"])
        now = float(self.clock())
        data = {"candidate": candidate, "pairs": pairs,
                "experiment": selected.get("ordinal"), "last_bar": None,
                "source_sha": _paper_source_sha(),
                "pending": None, "pending_recorded_at": None,
                "signal_queue": [],
                "held": None, "units": 0.0,
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
        now = float(self.clock())
        if not math.isfinite(now):
            raise ValueError("Invalid forward-paper observation clock")
        completed_end = int(now // BAR) * BAR
        end = completed_end if end is None else int(end)
        # Every paper sample must be observed in its own live completed-bar
        # slot. An older explicit end would silently backfill past candles as
        # prospective evidence, even when no order was placed on those bars.
        if end % BAR:
            raise ValueError("Forward-paper end is unaligned")
        if end != completed_end:
            raise ValueError("Forward-paper end is not the current completed boundary")
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM paper WHERE state='ACTIVE' "
                             "ORDER BY created ASC LIMIT 1").fetchone()
            if row is None:
                return {"status": "NO_ACTIVE_FORWARD_TRIAL"}
            data = json.loads(row["data"])
            # Legacy trials have no implementation provenance. Never count
            # their prior samples under newly revised paper/strategy rules.
            if data.get("source_sha") != _paper_source_sha():
                data["last_error"] = (
                    "Forward paper implementation changed or was not pinned; "
                    "start a new independent trial")
                db.execute("UPDATE paper SET state=?,data=?,updated=? WHERE id=?",
                           ("INVALIDATED_SOURCE", json.dumps(data), now, row["id"]))
                return {"status": "INVALIDATED_SOURCE", **data}
            # Require the entire observed candle to open AFTER the trial
            # was frozen. A bar already in progress at enqueue is not a
            # full prospective five-minute sample.
            if end - BAR < float(row["created"]):
                return {"status": "WAITING", **data}
            last = end-BAR
            if data["last_bar"] is not None and last <= data["last_bar"]:
                return {"status": "WAITING", **data}
            # A skipped observation cannot be backfilled into a pretend
            # real-time trade. Mark the gap and keep the paper position.
            if data["last_bar"] is not None and last-data["last_bar"] != BAR:
                data["pending"] = None
                data["pending_recorded_at"] = None
                data["signal_queue"] = []
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
                # A changed target must not erase an older signal that has
                # not yet reached its first causally eligible candle opening.
                # Keep a short FIFO of signal changes until each eligible
                # opening is observed. Legacy pending timestamps migrate once.
                queue = data.get("signal_queue")
                if queue is None:
                    queue = []
                    recorded_at = data.get("pending_recorded_at")
                    if recorded_at is not None:
                        queue.append({"target": data["pending"],
                                      "recorded_at": recorded_at})
                remaining = []
                for signal in queue:
                    if not isinstance(signal, dict):
                        continue
                    signal_at = signal.get("recorded_at")
                    if (not isinstance(signal_at, (int, float))
                            or not math.isfinite(signal_at)
                            or signal_at < float(row["created"])):
                        continue  # Invalid/legacy timestamps fail closed.
                    if signal_at >= last:
                        remaining.append(signal)
                        continue
                    target_to_fill = signal.get("target")
                    if (data["last_bar"] is None
                            or target_to_fill == data["held"]):
                        continue
                    if data["held"] is not None:
                        data["cash"] += data["units"]*candle[data["held"]]["open"]*(1-COST)
                        data["orders"] += 1
                        data["held"], data["units"] = None, 0.0
                    if target_to_fill is not None and data["cash"] >= 2.0:
                        data["units"] = data["cash"]*(1-COST)/candle[target_to_fill]["open"]
                        data["cash"], data["held"] = 0.0, target_to_fill
                        data["orders"] += 1
                data["signal_queue"] = remaining
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
                # Individually finite market prices can still overflow a
                # portfolio multiplication or benchmark ratio. Never persist
                # Infinity/NaN as forward evidence or emit invalid JSON.
                if not all(
                        isinstance(data.get(field), (int, float))
                        and math.isfinite(data[field])
                        for field in ("cash", "units", "equity", "peak",
                                      "drawdown", "net_return", "btc_return",
                                      "first_btc")):
                    raise ValueError("Nonfinite forward paper accounting")
                # Mirror simulate(): a frozen candidate decides only on its
                # configured rebalance bars. Calling _choose every five minutes
                # would forward-test a different strategy from the one selected.
                rebalance = data["candidate"][3]
                due = data["samples"] % rebalance == 0
                target = data["pending"]
                if due:
                    target = _choose(bars, len(bars), data["pairs"], data["candidate"])
                    # The historical simulator's persistent mode retains an
                    # incumbent while its lookback return remains acceptable.
                    if data["candidate"][0] == "persistent" and data["held"] is not None:
                        incumbent = data["held"]
                        lookback = data["candidate"][1]
                        threshold = data["candidate"][2]
                        previous = bars[-1]["prices"][incumbent]["close"]
                        old = bars[-lookback-1]["prices"][incumbent]["close"]
                        if previous / old - 1 >= -threshold:
                            target = incumbent
                observed_at = float(self.clock())
                if (not math.isfinite(observed_at)
                        or not end <= observed_at < end + BAR
                        or observed_at < float(row["created"])):
                    # Clock rollback and slow fetches crossing the next bar
                    # both invalidate the claimed live observation slot.
                    raise ValueError("Noncausal paper signal observation clock")
                if due and (target != data["pending"]
                            or data.get("pending_recorded_at") is None):
                    data["pending_recorded_at"] = observed_at
                    data["signal_queue"].append(
                        {"target": target, "recorded_at": observed_at})
                data["pending"] = target
                data["last_target"] = target
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
        if row is None:
            return {"status": "NO_ACTIVE_FORWARD_TRIAL"}
        data = json.loads(row["data"])
        if data.get("source_sha") != _paper_source_sha():
            # Preserve the historical verdict for inspection, but never
            # present old-code evidence as validation of the current code.
            return {"status": "INVALIDATED_SOURCE",
                    "historical_status": row["state"], **data}
        return {"status": row["state"], **data}
