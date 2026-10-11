"""BEAN's unattended, research-only scheduler for new completed hourly data.

The supervisor never accesses wallet rails and never places or authorizes orders.
It schedules one fresh-window experiment per hour, remembers attempted windows
across restarts and refreshes the independent read-only policy evidence.
"""
from __future__ import annotations

import threading
import time

from .bean_strategy_lab import HOUR
from .bean_five_minute import BAR

SETTLE_SECONDS = 300
POLL_SECONDS = 60
POLICY_REFRESH_SECONDS = 900
FAST_SETTLE_SECONDS = 60


class BeanResearchSupervisor:
    def __init__(self, lab, gate, *, clock=None, settle_seconds=SETTLE_SECONDS,
                 poll_seconds=POLL_SECONDS, fast_lab=None, paper=None):
        self.lab = lab
        self.gate = gate
        self.fast_lab = fast_lab
        self.paper = paper
        self._fast_action = "NOT_CONFIGURED"
        self._fast_error = None
        self.clock = clock or time.time
        self.settle_seconds = settle_seconds
        self.poll_seconds = poll_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last_error = None
        self._last_action = "WAITING_FOR_FRESH_CANDLES"
        self._last_policy_refresh = 0.0
        self._last_policy = {"status": "NOT_EVALUATED", "trade_effect": "NONE",
                             "reason": "BEAN has not checked fresh hourly candles yet"}
        with self.lab._lock, self.lab._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS bean_supervisor_meta ("
                       "name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO bean_supervisor_meta(name,value) "
                       "VALUES('enabled','1')")

    def _read(self, key, default=""):
        with self.lab._lock, self.lab._connect() as db:
            row = db.execute("SELECT value FROM bean_supervisor_meta WHERE name=?",
                             (key,)).fetchone()
            return row["value"] if row else default

    def _write(self, key, value):
        with self.lab._lock, self.lab._connect() as db:
            db.execute("INSERT INTO bean_supervisor_meta(name,value) VALUES(?,?) "
                       "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                       (key, str(value)))

    def enabled(self):
        return self._read("enabled", "1") == "1"

    def set_enabled(self, enabled):
        self._write("enabled", "1" if enabled else "0")
        with self._lock:
            self._last_action = ("WAITING_FOR_FRESH_CANDLES" if enabled
                                 else "PAUSED_BY_USER")
        if enabled:
            self.start()
        return self.status()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="bean-research-watch",
                                            daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            self._stop.wait(self.poll_seconds)

    def tick(self):
        """One bounded iteration; safe for deterministic, network-free tests."""
        if not self.enabled():
            with self._lock:
                self._last_action = "PAUSED_BY_USER"
            return
        now = float(self.clock())
        if self.fast_lab is not None and self.paper is not None:
            self.tick_fast(now)
        end = int(now // HOUR * HOUR)
        if now < end + self.settle_seconds:
            with self._lock:
                self._last_action = "WAITING_FOR_FRESH_CANDLES"
            return

        # Refresh public candle policy on a separate cadence. An HTTP error is
        # observed as unavailable evidence; it never becomes a trading signal.
        with self._lock:
            should_refresh = (not self._last_policy_refresh or
                              now - self._last_policy_refresh >= POLICY_REFRESH_SECONDS)
            if should_refresh:
                self._last_policy_refresh = now
        if should_refresh:
            try:
                value = self.gate.assess()
            except Exception as exc:
                value = {"status": "DATA_UNAVAILABLE", "trade_effect": "NONE",
                         "reason": f"Read-only evaluation: {type(exc).__name__}"}
            with self._lock:
                self._last_policy = dict(value)

        # Never start two overlapping lab experiments; prior failed trials are
        # also counted so an unavailable provider is not hammered on restart.
        if self.lab.status()["running"]:
            with self._lock:
                self._last_action = "EXPERIMENT_ALREADY_RUNNING"
            return
        with self.lab._lock, self.lab._connect() as db:
            row = db.execute("SELECT value FROM bean_supervisor_meta "
                             "WHERE name='last_attempted_bar'").fetchone()
            last = int(row["value"]) if row else -1
            if last >= end:
                with self._lock:
                    self._last_action = "WAITING_FOR_FRESH_CANDLES"
                return
            db.execute("INSERT INTO bean_supervisor_meta(name,value) "
                       "VALUES('last_attempted_bar',?) "
                       "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (str(end),))
        try:
            self.lab.start()
            with self._lock:
                self._last_action = "AUTONOMOUS_EXPERIMENT_STARTED"
                self._last_error = None
        except RuntimeError as exc:
            # Another actor may have clicked RUN between our check and start.
            with self._lock:
                self._last_action = "EXPERIMENT_ALREADY_RUNNING"
                self._last_error = str(exc)[:180]

    def status(self):
        now = float(self.clock())
        end = int(now // HOUR * HOUR)
        last = int(self._read("last_attempted_bar", "-1"))
        with self._lock:
            thread_active = bool(self._thread and self._thread.is_alive())
            action = self._last_action
            error = self._last_error
            policy = dict(self._last_policy)
            last_policy_refresh = self._last_policy_refresh or None
        next_bar = (end + self.settle_seconds if last < end else
                    end + HOUR + self.settle_seconds)
        return {
            "enabled": self.enabled(), "worker_running": thread_active,
            "last_action": action, "last_error": error,
            "last_attempted_bar": last if last >= 0 else None,
            "next_experiment_at": max(now, next_bar),
            "schedule": "Once per newly completed Coinbase hourly candle, "
                        "5 minutes after the hour; never trades",
            "policy": policy, "last_policy_check_at": last_policy_refresh,
        }
