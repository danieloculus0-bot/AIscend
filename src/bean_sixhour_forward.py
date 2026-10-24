"""Causal prospective BEAN/Coinbase public-paper ledger for frozen candidates.

Research only. No Coinbase credentials, wallet imports or order submissions.
All hypothetical entry opens occur AFTER the signal was committed, and scores
appear only after the corresponding six-hour candle closes.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import hashlib
import json
import math
import sqlite3
import time

from .bean_profit_lab import GENESIS, bean_action, bean_context, canonical, chain, signal
from .bean_six_hour_universe import BAR, fetch_history
from .core import app_data_dir


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _append(db, kind, *, product=None, state=None, **details):
    # Last tip and row count are updated in the SAME SQLite transaction as the
    # event. A full hash-chain scan is performed by inspect() before any write.
    # This makes thousands of observations O(N), rather than O(N**2).
    tip_row = db.execute("SELECT v FROM meta WHERE k='ledger_tip'").fetchone()
    seq_row = db.execute("SELECT v FROM meta WHERE k='ledger_count'").fetchone()
    if tip_row is not None and seq_row is not None:
        tip, sequence = tip_row[0], int(seq_row[0])
    else:
        events = [json.loads(row[0]) for row in
                  db.execute("SELECT data FROM events ORDER BY seq")]
        tip, sequence = chain(events), len(events)
    entry = {"seq": sequence, "prev": tip, "type": kind, **details}
    if product is not None:
        entry["product"] = product
    if state is not None:
        encoded = canonical(state)
        entry["state_sha"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        db.execute(
            "INSERT INTO positions(product,state) VALUES(?,?) "
            "ON CONFLICT(product) DO UPDATE SET state=excluded.state",
            (product, encoded))
    entry["hash"] = digest(entry)
    db.execute("INSERT INTO events(seq,data) VALUES(?,?)",
               (entry["seq"], canonical(entry)))
    db.execute("INSERT INTO meta(k,v) VALUES('ledger_tip',?) "
               "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (entry["hash"],))
    db.execute("INSERT INTO meta(k,v) VALUES('ledger_count',?) "
               "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(sequence + 1),))
    return entry["hash"]


def _verify(db):
    events = [json.loads(row[0]) for row in
              db.execute("SELECT data FROM events ORDER BY seq")]
    tip = chain(events)
    anchor_tip = db.execute("SELECT v FROM meta WHERE k='ledger_tip'").fetchone()
    anchor_count = db.execute("SELECT v FROM meta WHERE k='ledger_count'").fetchone()
    if anchor_tip is not None and anchor_tip[0] != tip:
        raise ValueError("SQLite BEAN ledger tip differs from recorded checkpoint")
    if anchor_count is not None and int(anchor_count[0]) != len(events):
        raise ValueError("SQLite BEAN ledger event count differs from checkpoint")
    last = {e["product"]: e["state_sha"]
            for e in events if "state_sha" in e}
    persisted = dict(db.execute("SELECT product,state FROM positions"))
    if set(last) != set(persisted):
        raise ValueError("BEAN state is missing from append-only evidence")
    for product, state in persisted.items():
        if hashlib.sha256(state.encode("utf-8")).hexdigest() != last[product]:
            raise ValueError("BEAN state was modified without ledger evidence")
    return tip


def _history(raw, now, *, strict_features=True):
    end = now // BAR * BAR
    rows = sorted((dict(r) for r in raw.values()
                   if int(r["ts"]) + BAR <= end),
                  key=lambda r: r["ts"])
    for r in rows:
        if (r["ts"] % BAR or
                not all(math.isfinite(float(r[k])) for k in ("open", "close")) or
                min(float(r["open"]), float(r["close"])) <= 0):
            raise ValueError("Invalid six-hour Coinbase candle")
    if len(rows) < 44 or rows[-1]["ts"] != end - BAR:
        raise ValueError("Current completed six-hour candle missing")
    if strict_features and any(rows[i]["ts"] != rows[i-1]["ts"] + BAR
           for i in range(len(rows)-43, len(rows))):
        raise ValueError("Missing required contiguous feature history")
    return rows


def _signal(db, candidate, state, rows, now, *, clock):
    if state.get("pending") or state["blocked"]:
        return
    policy = candidate["policy"]
    if policy == "bean_q":
        context = bean_context(rows, BAR)
        target = bean_action(state["q"], context, state["held"])
    else:
        context = None
        target = signal(rows, policy, BAR)
    entry_ts = (now // BAR + 1) * BAR
    # A public history request can cross the next candle open. Timestamp
    # the completed decision, not the earlier clock sample before fetching.
    selected_at = float(clock())
    if not math.isfinite(selected_at) or selected_at < now:
        raise ValueError("Invalid prospective signal observation clock")
    if selected_at >= entry_ts:
        return  # The planned entry already opened; never backdate a fill.
    if entry_ts <= state["frozen_at_unix"]:
        raise ValueError("Cannot fill at an already-open historical candle")
    state["pending"] = {
        "selected_at": selected_at, "entry_ts": entry_ts,
        "context": context, "target": target,
        "observed_cutoff": rows[-1]["ts"] + BAR,
    }
    _append(db, "preentry_signal", product=candidate["product"],
            state=state, pending=state["pending"])


def _score(db, candidate, state, row):
    ts = int(row["ts"])
    if ts != state["last_seen_ts"] + BAR:
        state["blocked"] = "DATA_GAP"
        _append(db, "data_gap", product=candidate["product"], state=state,
                expected_ts=state["last_seen_ts"] + BAR, observed_ts=ts)
        return
    # A candle opened before the immutable freeze is historical, even when
    # it closes after the freeze. Advance the chronological anchor without
    # counting it toward the forward gate, fees, P/L or BEAN Q rewards.
    if ts < state["frozen_at_unix"]:
        state["last_seen_ts"] = ts
        state["last_close"] = float(row["close"])
        _append(db, "pre_freeze_candle_excluded",
                product=candidate["product"], state=state,
                candle_start=ts, observed_after_ts=ts + BAR,
                frozen_at=state["frozen_at_unix"])
        return
    opening, close = float(row["open"]), float(row["close"])
    # Start the passive benchmark at the first observed forward candle, not
    # at the strategy's first trade. Otherwise time in cash hides market moves.
    if state["benchmark_first_open"] is None:
        state["benchmark_first_open"] = opening
    cost = state["cost"]
    was_held = state["held"]
    previous_close = state["last_close"]
    if was_held:
        state["equity"] *= opening / previous_close

    pending = state["pending"]
    if pending and ts >= pending["entry_ts"]:
        if ts != pending["entry_ts"] or pending["selected_at"] >= ts:
            state["blocked"] = "MISSED_COMMITTED_ENTRY"
            _append(db, "missed_entry", product=candidate["product"],
                    state=state, observed_ts=ts)
            return
        target = pending["target"]
        if target not in (0, 1):
            raise ValueError("Invalid committed target")
        if target != was_held:
            pre_fee_equity = state["equity"]
            state["equity"] *= 1 - cost
            state["orders"] += 1
            if was_held:
                state["roundtrips"] += 1
                state["wins"] += int(
                    state["equity"] > state.get("entry_equity", 1.))
                state["entry_equity"] = None
            else:
                state["entry_equity"] = pre_fee_equity
            state["held"] = target
        state["pending"] = None

    if state["held"]:
        state["equity"] *= close / opening

    if pending and ts == pending["entry_ts"] and candidate["policy"] == "bean_q":
        context = pending["context"]
        scores = state["q"].setdefault(context, {"long": 0., "cash": 0.})
        held_gap = opening / previous_close if was_held else 1.
        for action, gross in (("long", close / opening - 1), ("cash", 0.)):
            target = int(action == "long")
            side_cost = cost if target != was_held else 0.
            reward_bps = (held_gap*(1-side_cost)*(1+gross)-1)*10000
            scores[action] += .08*(reward_bps - scores.get(action, 0.))
        state["q_updates"] += 1
        _append(db, "postclose_q_update", product=candidate["product"],
                state=state, candle_start=ts, observed_after_ts=ts+BAR,
                context=context, q=scores)

    state["last_close"] = close
    state["last_seen_ts"] = ts
    state["forward_bars"] += 1
    liquidation = state["equity"]*(1-cost if state["held"] else 1.)
    state["peak"] = max(state["peak"], liquidation)
    state["max_drawdown"] = max(
        state["max_drawdown"], 1-liquidation/state["peak"])
    state["equity_liquidatable"] = liquidation
    if state["benchmark_first_open"] is not None:
        state["buy_hold_net"] = (
            close/state["benchmark_first_open"]*(1-cost)**2 - 1)
    _append(db, "completed_candle_mark", product=candidate["product"],
            state=state, candle_start=ts, observed_after_ts=ts+BAR,
            liquidation_value=round(liquidation, 10))


def inspect(manifest_path, db_path, *, clock=None, history_fetcher=None):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("schema") != "aiscend.bean.sixhour.prospective.freeze.v1":
        raise ValueError("Unknown BEAN prospective freeze manifest")
    if manifest.get("promotion_authorized") is not False:
        raise ValueError("Cannot accept live-promotion authority from a manifest")
    # Reject corrupt fee assumptions before creating a paper ledger.
    try:
        fee = float(manifest["fee_bps_per_side"])
        spread = float(manifest["spread_bps_roundtrip"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid prospective fee model") from exc
    if (not math.isfinite(fee) or not math.isfinite(spread)
            or fee < 0 or spread < 0 or fee + spread / 2 >= 10000):
        raise ValueError("Invalid prospective fee model")
    clock_fn = clock or time.time
    now = int(clock_fn())
    if now <= manifest["frozen_at_unix"]:
        return {"status": "PRE_FREEZE", "promotion_authorized": False}
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_sha = digest(manifest)

    with closing(sqlite3.connect(db_path, timeout=30.0)) as db, db:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        # Serialize competing Python terminals before verifying and appending.
        # The lock and all ledger changes are committed or rolled back together.
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS positions("
                   "product TEXT PRIMARY KEY,state TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS events("
                   "seq INTEGER PRIMARY KEY,data TEXT NOT NULL)")
        row = db.execute("SELECT v FROM meta WHERE k='manifest_sha'").fetchone()
        if row and row[0] != manifest_sha:
            raise ValueError("Cannot change BEAN freeze, Q model or fee assumptions")
        if row is None:
            db.execute("INSERT INTO meta(k,v) VALUES('manifest_sha',?)",
                       (manifest_sha,))
            _append(db, "freeze_preregistered", manifest_sha=manifest_sha,
                    frozen_at=manifest["frozen_at_unix"])
        _verify(db)
        reports = {}
        for candidate in manifest["candidates"]:
            product = candidate["product"]
            stored = db.execute(
                "SELECT state FROM positions WHERE product=?", (product,)
            ).fetchone()
            state = json.loads(stored[0]) if stored else None
            if state and state["blocked"]:
                reports[product] = {"status": state["blocked"],
                                    "promotion_authorized": False}
                continue
            try:
                raw = (
                    history_fetcher(product, days=45, clock=lambda: now)
                    if history_fetcher else
                    fetch_history(product, days=45, clock=lambda: now))
                # Recheck feature-window continuity on EVERY observation. A public
                # candle can disappear from a later API response even after
                # initialization; silently shortening the lookback changes a
                # frozen signal and could produce an unsupported paper fill.
                rows = _history(raw, now, strict_features=True)
            except Exception as exc:
                reports[product] = {
                    "status": "DATA_UNAVAILABLE",
                    "reason_type": type(exc).__name__,
                    "promotion_authorized": False}
                continue

            if state is None:
                state = {
                    "frozen_at_unix": manifest["frozen_at_unix"],
                    "last_seen_ts": rows[-1]["ts"],
                    "last_close": float(rows[-1]["close"]),
                    "initialized_at": now, "blocked": None,
                    "q": candidate.get("q_seed") or {},
                    "equity": 1., "equity_liquidatable": 1.,
                    "held": 0, "entry_equity": None, "pending": None,
                    "orders": 0, "roundtrips": 0, "wins": 0,
                    "q_updates": 0, "forward_bars": 0,
                    "peak": 1., "max_drawdown": 0.,
                    "cost": (manifest["fee_bps_per_side"] +
                             manifest["spread_bps_roundtrip"]/2)/10000,
                    "benchmark_first_open": None, "buy_hold_net": None,
                }
                _append(db, "first_prospective_observation",
                        product=product, state=state, observed_at=now,
                        last_completed_start=rows[-1]["ts"])
            else:
                unseen = [bar for bar in rows
                          if int(bar["ts"]) > state["last_seen_ts"]]
                for bar in unseen:
                    _score(db, candidate, state, bar)
                    if state["blocked"]:
                        break
            if not state["blocked"]:
                _signal(db, candidate, state, rows, now, clock=clock_fn)
            net = state["equity_liquidatable"] - 1.
            passive = state["buy_hold_net"]
            qualifies = bool(
                state["forward_bars"] >= manifest["min_forward_completed_bars"]
                and state["roundtrips"] >= manifest["min_realized_roundtrips"]
                and net > manifest["min_excess_return_vs_cash"]
                and passive is not None
                and net > passive + manifest["min_excess_return_vs_buy_hold"]
                and state["max_drawdown"] <= manifest["max_forward_drawdown"])
            reports[product] = {
                "status": state["blocked"] or
                    ("FORWARD_PAPER_GATE_PASS" if qualifies else "COLLECTING"),
                "forward_bars": state["forward_bars"],
                "roundtrips": state["roundtrips"],
                "orders": state["orders"],
                "q_updates": state["q_updates"],
                "fee_net_paper_return": round(net, 8),
                "buy_hold_net": round(passive, 8) if passive is not None else None,
                "max_drawdown": round(state["max_drawdown"], 8),
                "pending": state["pending"],
                "promotion_authorized": False,
            }
        tip = _verify(db)
        return {
            "status": "OBSERVED", "as_of_unix": now,
            "manifest_sha": manifest_sha, "ledger_tip": tip,
            "markets": reports, "promotion_authorized": False,
            "note": "Prospective hypothetical Coinbase public candles only; no live fill or verified future profit",
        }


def persistent_db_path():
    """Persistent local Python application state, never an ephemeral CI artifact."""
    return app_data_dir() / "bean-sixhour-prospective.sqlite3"


def verify_database(manifest_path, db_path):
    """Read-only SQLite health check, frozen-policy identity and ledger replay."""
    target = Path(db_path)
    if not target.is_file():
        raise FileNotFoundError("BEAN prospective SQLite ledger has not been initialized")
    expected = digest(json.loads(Path(manifest_path).read_text(encoding="utf-8")))
    # mode=ro prevents accidentally creating or modifying the paper ledger.
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=30.0)) as db:
        check = db.execute("PRAGMA integrity_check").fetchone()
        if check is None or check[0] != "ok":
            raise ValueError("SQLite integrity_check failed")
        meta = db.execute("SELECT v FROM meta WHERE k='manifest_sha'").fetchone()
        if meta is None or meta[0] != expected:
            raise ValueError("SQLite BEAN ledger manifest mismatch")
        tip = _verify(db)
        event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        states = {}
        for product, encoded in db.execute("SELECT product,state FROM positions"):
            state = json.loads(encoded)
            states[product] = {
                "last_completed_candle": state["last_seen_ts"],
                "forward_bars": state["forward_bars"],
                "orders": state["orders"],
                "roundtrips": state["roundtrips"],
                "q_updates": state["q_updates"],
                "blocked": state.get("blocked"),
                "pending_entry_at": (state.get("pending") or {}).get("entry_ts"),
            }
        return {
            "status": "LEDGER_VERIFIED", "db": str(target),
            "events": event_count, "ledger_tip": tip,
            "manifest_sha": expected, "markets": states,
            "promotion_authorized": False,
        }


def backup_database(manifest_path, db_path, destination):
    """Use SQLite's online backup API, not a raw copy of an active WAL file."""
    original = verify_database(manifest_path, db_path)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation: a second worker cannot silently overwrite proof.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() +
                                     "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
        restored = verify_database(manifest_path, destination)
        if restored["ledger_tip"] != original["ledger_tip"]:
            raise ValueError("SQLite backup ledger does not match original")
        checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
        destination.with_suffix(destination.suffix + ".sha256").write_text(
            checksum + "  " + destination.name + "\n", encoding="ascii")
        return {"status": "BACKUP_VERIFIED", "path": str(destination),
                "sha256": checksum, "ledger_tip": restored["ledger_tip"],
                "events": restored["events"], "promotion_authorized": False}
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def watch(manifest_path, db_path, *, backup_dir=None,
          clock=None, sleep=None, observer=None, max_cycles=None,
          reporter=None):
    """Run a persistent six-hour paper observer in a dedicated Python shell.

    Each cycle calls the SAME SQLite database. Offline time is not invented;
    saved pending signals and verified positions survive process restarts.
    """
    clock = clock or time.time
    sleep = sleep or time.sleep
    observer = observer or inspect
    reporter = reporter or print
    backup_dir = Path(backup_dir) if backup_dir is not None else None
    previous_tip = None
    count = 0
    while max_cycles is None or count < max_cycles:
        report = observer(manifest_path, db_path, clock=clock)
        reporter(json.dumps(report, sort_keys=True))
        count += 1
        tip = report.get("ledger_tip")
        if backup_dir is not None and tip and tip != previous_tip:
            archive = backup_dir / ("bean-ledger-" + tip[:16] + ".sqlite3")
            if archive.exists():
                existing = verify_database(manifest_path, archive)
                if existing["ledger_tip"] != tip:
                    raise ValueError("Existing backup does not match event hash")
            else:
                backup_database(manifest_path, db_path, archive)
        previous_tip = tip
        if max_cycles is not None and count >= max_cycles:
            break
        now = float(clock())
        next_close = (int(now) // BAR + 1) * BAR + 120
        # Missing exchange data merits a bounded retry; otherwise wait for the
        # next newly completed six-hour candle, not a stale-data re-run storm.
        unavailable = any(v.get("status") == "DATA_UNAVAILABLE"
                          for v in report.get("markets", {}).values())
        until = min(next_close, now + 300) if unavailable else next_close
        sleep(max(1.0, until - now))
    return count


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(
        "research/bean_frozen_prospective_20261023_fee90.json"))
    parser.add_argument("--db", type=Path, default=None,
                        help="Optional override. By default, persist in LocalAppData.")
    parser.add_argument("--output", type=Path,
                        help="One-shot JSON report file (not the SQLite ledger)")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--watch", action="store_true",
                         help="Observe new completed six-hour candles continuously")
    actions.add_argument("--verify-only", action="store_true",
                         help="Verify existing SQLite state without market requests")
    actions.add_argument("--backup-to", type=Path,
                         help="Create and verify a SQLite backup without market requests")
    args = parser.parse_args(argv)
    db_path = args.db or persistent_db_path()

    if args.watch:
        if args.output:
            parser.error("--output is for one-shot observation, not --watch")
        print("BEAN prospective Python ledger:", db_path, flush=True)
        print("No orders or wallet operations; Ctrl+C stops the observer.",
              flush=True)
        try:
            watch(args.manifest, db_path,
                  backup_dir=app_data_dir() / "bean-ledger-backups")
        except KeyboardInterrupt:
            print("BEAN observer stopped; SQLite records retained.")
        return 0
    if args.verify_only:
        result = verify_database(args.manifest, db_path)
    elif args.backup_to:
        result = backup_database(args.manifest, db_path, args.backup_to)
    else:
        result = inspect(args.manifest, db_path)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
