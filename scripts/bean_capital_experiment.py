"""Research-only $1,000 BEAN capital experiment with replayable SQLite evidence.

Trains the ACTUAL AIscend BeanAdaptiveGrowthGate._apply_bar Q-policy on synthetic
training markets. Freezes its Q table BEFORE a disjoint 500-episode holdout;
compares its capital to an unchanged production price-action baseline, buy/hold,
and cash on identical synthetic paths. Every evaluated bar is hash-chained.
BEAN Core's optional Bridge review sees only aggregate evidence and never
selects actions or authorizes an order. No Coinbase credentials or order APIs.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics

from scripts.bean_optimization_lab import REGIMES, market
from src.bean_growth_adaptive import (
    BeanAdaptiveGrowthGate, _choose, _features, _fresh_state, _position,
)
from src.primary_strategy import proposals, exit_reason
from src.bean_rotation_economics import score_route

SCHEMA = "aiscend.bean.capital.evidence.v1"
ROLES = ("BEAN Q-policy trainer", "paired independent evaluator",
         "SQLite receipt auditor", "optional BEAN Core Bridge reviewer")
MODES = ("bean_frozen", "price_action", "buy_hold", "cash")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def prepare(db):
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE training(
          episode TEXT PRIMARY KEY, pre_q_sha TEXT NOT NULL,
          post_q_sha TEXT NOT NULL, updates INTEGER NOT NULL,
          q_snapshot TEXT NOT NULL);
        CREATE TABLE evaluations(
          episode TEXT PRIMARY KEY, seed INTEGER NOT NULL,
          regime TEXT NOT NULL, policy TEXT NOT NULL, initial_usd REAL NOT NULL,
          ending_usd REAL NOT NULL, max_drawdown REAL NOT NULL,
          trades INTEGER NOT NULL, friction_paid_usd REAL NOT NULL,
          last_event_sha TEXT NOT NULL);
        CREATE TABLE events(
          episode TEXT NOT NULL, seq INTEGER NOT NULL,
          candle INTEGER NOT NULL, type TEXT NOT NULL,
          payload TEXT NOT NULL, previous_sha TEXT NOT NULL,
          event_sha TEXT NOT NULL,
          PRIMARY KEY(episode,seq));
    """)


class Receipt:
    def __init__(self, db, episode):
        self.db, self.episode = db, episode
        self.seq = 0
        self.previous = "0" * 64

    def append(self, candle, kind, payload):
        self.seq += 1
        encoded = canonical(payload)
        chain = {
            "episode": self.episode, "seq": self.seq,
            "candle": candle, "kind": kind,
            "payload": encoded, "previous": self.previous,
        }
        current = digest(chain)
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
            (self.episode, self.seq, candle, kind, encoded,
             self.previous, current))
        self.previous = current


def train_q(*, base_seed, seed_count, periods):
    """No holdout prices enter this function; carry only the learned Q state."""
    q, visits, wins, trials = {}, {}, {}, {}
    receipts = []
    for i in range(seed_count):
        seed = base_seed + i
        for r, regime in enumerate(REGIMES):
            state = _fresh_state()
            state["q"], state["visits"] = q, visits
            state["wins"], state["win_trials"] = wins, trials
            before = digest(q)
            prices = market(seed, regime, periods=periods)
            # Preserve causal order: action after previous close, fill at next open.
            # Separate episode clocks prevent identical hash-based exploration
            # salt on every path, while preserving reproducible run seeds.
            epoch = (i * len(REGIMES) + r + 1) * 1000000
            for t in range(1, len(prices)):
                BeanAdaptiveGrowthGate._apply_bar(
                    state, {"open": prices[t - 1], "close": prices[t]},
                    epoch + t * 300, "SYNTH-USD")
            q, visits = state["q"], state["visits"]
            wins, trials = state["wins"], state["win_trials"]
            updates = sum(sum(v.values()) for v in visits.values())
            receipts.append((f"train:{seed}:{regime}", before,
                             digest(q), updates, canonical(q)))
    return q, receipts


def baseline_target(closes, *, held, entry_price, peak_price,
                    maturity_step, step, spread_bps, fee_bps):
    if held:
        row = {"product": "SYNTH-USD", "return_5m": 0,
               "return_1h": 0, "return_6h": 0}
        if len(closes) >= 73:
            row.update(return_5m=closes[-1]/closes[-2]-1,
                       return_1h=closes[-1]/closes[-13]-1,
                       return_6h=closes[-1]/closes[-73]-1)
        reason = exit_reason(
            "SYNTH-USD", {"asset_universe": {"scored": [row]}},
            current_mark_price=closes[-1],
            observed_peak_price=peak_price,
            verified_entry_price=entry_price,
            deadline_reached=step >= maturity_step)
        return (0, "exit:" + reason) if reason else (1, "hold_position")
    if len(closes) < 73:
        return 0, "warming_up"
    row = {
        "product": "SYNTH-USD",
        "return_5m": closes[-1]/closes[-2]-1,
        "return_1h": closes[-1]/closes[-13]-1,
        "return_6h": closes[-1]/closes[-73]-1,
        # The synthetic generator has no measured volume; don't invent it.
        "volume_ratio": 0.0, "spread_bps": spread_bps}
    routes = [
        score_route(p, taker_fee_bps=fee_bps)
        for p in proposals(row)]
    winner = max(routes, key=lambda p: p["net_projected_bps"],
                 default=None)
    if winner and winner["net_projected_bps"] > 0:
        return 1, "entry:" + winner["strategy"]
    return 0, "no_positive_costed_signal"


def evaluate_path(db, *, seed, regime, mode, prices, q,
                  capital, fee_bps, spread_bps, slippage_bps):
    episode = f"holdout:{seed}:{regime}:{mode}"
    record = Receipt(db, episode)
    friction = (fee_bps + spread_bps / 2 + slippage_bps) / 10000
    if not 0 <= friction < 1:
        raise ValueError("Invalid per-side friction")
    cash, qty, pending = float(capital), 0.0, 0
    entry_price, peak_price, maturity = 0.0, 0.0, 0
    peak, drawdown, trades, total_friction = capital, 0.0, 0, 0.0
    returns = []
    for t in range(1, len(prices)):
        opening, closing = prices[t - 1], prices[t]
        held_before = qty > 0
        if pending != int(held_before):
            if pending:
                spent = cash
                qty = spent * (1 - friction) / opening
                cash = 0.0
                entry_price = spent / qty
                peak_price = opening
                maturity = t + 72
                charged = spent * friction
                side = "BUY"
            else:
                gross = qty * opening
                charged = gross * friction
                cash += gross - charged
                qty, entry_price, peak_price, maturity = 0.0, 0.0, 0.0, 0
                side = "SELL"
            trades += 1
            total_friction += charged
            record.append(t, "FILL", {
                "side": side, "mid_open": opening, "friction": charged,
                "cash_after": cash, "quantity_after": qty})
        if qty:
            peak_price = max(peak_price, closing)
        nav = cash + qty * closing * (1 - friction if qty else 1)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1 - nav / peak)
        returns.append(closing / opening - 1)
        # This bar is now completed; only its close/history can choose NEXT bar.
        if t == len(prices) - 2:
            target, reason = 0, "fixed_horizon_liquidation"
        elif mode == "cash":
            target, reason = 0, "cash_control"
        elif mode == "buy_hold":
            target, reason = 1, "buy_hold_control"
        elif mode == "bean_frozen":
            context = _features(returns)
            action = _choose({"q": q}, context, explore=False, seed=0)
            target, reason = _position(action, returns), "q:" + action
        elif mode == "price_action":
            target, reason = baseline_target(
                prices[:t + 1], held=bool(qty),
                entry_price=entry_price, peak_price=peak_price,
                maturity_step=maturity, step=t,
                spread_bps=spread_bps, fee_bps=fee_bps)
        else:
            raise ValueError("Unknown evaluation mode")
        record.append(t, "DECISION", {
            "target": target, "reason": reason,
            "cash": round(cash, 8), "equity": round(nav, 8)})
        pending = target
    # Use the predeclared extra unopened bar's open for final liquidation.
    if qty:
        gross = qty * prices[-1]
        charged = gross * friction
        cash += gross - charged
        trades += 1
        total_friction += charged
        record.append(len(prices), "FILL", {
            "side": "SELL", "mid_open": prices[-1],
            "friction": charged, "cash_after": cash, "quantity_after": 0})
        qty = 0
    if not math.isfinite(cash) or cash < 0 or qty:
        raise AssertionError("Invalid final simulated accounting")
    drawdown = max(drawdown, 1 - cash / peak)
    record.append(len(prices), "FINAL", {
        "start": capital, "end": cash, "trades": trades,
        "charged_friction": total_friction, "max_drawdown": drawdown})
    db.execute(
        "INSERT INTO evaluations VALUES(?,?,?,?,?,?,?,?,?,?)",
        (episode, seed, regime, mode, capital, cash, drawdown,
         trades, total_friction, record.previous))
    return cash, drawdown, trades


def summarize(db, *, capital, train_count, holdout_count, q):
    rows = db.execute(
        "SELECT seed,regime,policy,ending_usd,max_drawdown,trades "
        "FROM evaluations").fetchall()
    modes = {}
    for mode in MODES:
        subset = [r for r in rows if r[2] == mode]
        amounts = [r[3] for r in subset]
        modes[mode] = {
            "runs": len(amounts),
            "mean_end_usd": round(statistics.mean(amounts), 4),
            "median_end_usd": round(statistics.median(amounts), 4),
            "profitable_runs": sum(v > capital for v in amounts),
            "median_max_drawdown_pct": round(
                100 * statistics.median(r[4] for r in subset), 4),
            "mean_trades": round(statistics.mean(r[5] for r in subset), 3),
        }
    paired = {}
    for row in rows:
        paired.setdefault((row[0], row[1]), {})[row[2]] = row[3]
    deltas = [items["bean_frozen"] - items["price_action"]
              for items in paired.values()]
    return {
        "schema": SCHEMA, "research_only": True,
        "market_source": "deterministic_synthetic_regime_prices",
        "historical_coinbase_prices_used": False,
        "exchange_orders": 0, "account_credentials_read": False,
        "initial_bankroll_usd_per_run": capital,
        "training_episodes": train_count * len(REGIMES),
        "holdout_market_episodes": holdout_count * len(REGIMES),
        "evaluation_episodes": len(rows), "regimes": list(REGIMES),
        "roles": list(ROLES),
        "q_sha256": digest(q),
        "q_contexts": len(q),
        "scoreboard": modes,
        "paired_mean_bean_minus_price_action_usd": round(
            statistics.mean(deltas), 4),
        "paired_bean_beats_price_action": sum(x > 0 for x in deltas),
        "bean_holdout_total_orders": sum(
            r[5] for r in rows if r[2] == "bean_frozen"),
        "bean_net_alpha_over_cash_mean_usd": round(
            modes["bean_frozen"]["mean_end_usd"] - capital, 4),
        "trading_skill_demonstrated": (
            modes["bean_frozen"]["mean_trades"] > 0
            and modes["bean_frozen"]["mean_end_usd"] > capital
            and modes["bean_frozen"]["mean_end_usd"]
                > modes["price_action"]["mean_end_usd"]),
        "adopt_for_live_trading": False,
        "limitation": "Actual BEAN adaptive Q updates only on TRAINING synthetic "
                      "candles. Frozen Q and conventional price action get the "
                      "SAME unseen holdout paths and all-in modeled costs. "
                      "This does not establish real Coinbase profitability.",
    }


def verify_ledger(path):
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute(
            "SELECT episode, initial_usd, ending_usd, last_event_sha "
            "FROM evaluations ORDER BY episode").fetchall()
        checked = 0
        for episode, initial, ending, expected in rows:
            previous = "0" * 64
            final = None
            last_seq = 0
            for seq, candle, kind, payload, prev, sha in db.execute(
                "SELECT seq,candle,type,payload,previous_sha,event_sha "
                "FROM events WHERE episode=? ORDER BY seq", (episode,)):
                if seq != last_seq + 1 or prev != previous:
                    raise AssertionError("Ledger event ordering/chain invalid")
                actual = digest({"episode": episode, "seq": seq,
                                 "candle": candle, "kind": kind,
                                 "payload": payload, "previous": prev})
                if sha != actual:
                    raise AssertionError("Ledger SHA-256 mismatch")
                previous, last_seq = sha, seq
                checked += 1
                if kind == "FINAL":
                    final = json.loads(payload)
            if previous != expected or final is None:
                raise AssertionError("Missing final receipt")
            if (abs(final["end"] - ending) > 1e-7
                    or abs(final["start"] - initial) > 1e-7):
                raise AssertionError("Final capital receipt mismatch")
        return {"verified_episodes": len(rows),
                "verified_events": checked, "hash_chains_valid": True}


def experiment(*, db_path, report_path, seed=26010, train_seeds=24,
               holdout_seeds=125, periods=300, capital=1000.0,
               fee_bps=60.0, spread_bps=10.0, slippage_bps=5.0,
               core_dir=None):
    if not 1 <= train_seeds <= 10000 or not 1 <= holdout_seeds <= 10000:
        raise ValueError("Invalid simulation iteration counts")
    if periods < 150 or not math.isfinite(capital) or capital <= 0:
        raise ValueError("Invalid horizon or initial bankroll")
    if not all(math.isfinite(x) and 0 <= x <= 1000
               for x in (fee_bps, spread_bps, slippage_bps)):
        raise ValueError("Invalid cost assumptions")
    db_path, report_path = Path(db_path), Path(report_path)
    if db_path.exists() or report_path.exists():
        raise FileExistsError("Never overwrite previous experiment evidence")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        prepare(db)
        configs = {
            "schema": SCHEMA, "seed": seed, "train_seeds": train_seeds,
            "holdout_seed_offset": 1000000,
            "holdout_seeds": holdout_seeds, "periods": periods,
            "capital": capital, "fee_bps": fee_bps,
            "spread_bps": spread_bps, "slippage_bps": slippage_bps,
            "source": "production BEAN Q updater + AIscend price-action baseline",
        }
        db.executemany("INSERT INTO meta VALUES(?,?)",
                       [(k, canonical(v)) for k, v in configs.items()])
        q, training_receipts = train_q(
            base_seed=seed, seed_count=train_seeds, periods=periods)
        db.executemany("INSERT INTO training VALUES(?,?,?,?,?)",
                       training_receipts)
        frozen = canonical(q)
        for i in range(holdout_seeds):
            episode_seed = seed + 1000000 + i
            for regime in REGIMES:
                prices = market(episode_seed, regime, periods=periods + 1)
                for mode in MODES:
                    evaluate_path(
                        db, seed=episode_seed, regime=regime,
                        mode=mode, prices=prices, q=q,
                        capital=capital, fee_bps=fee_bps,
                        spread_bps=spread_bps, slippage_bps=slippage_bps)
            db.commit()
        if canonical(q) != frozen:
            raise AssertionError("Holdout mutated frozen Q weights")
        report = summarize(
            db, capital=capital,
            train_count=train_seeds, holdout_count=holdout_seeds, q=q)
        report["ledger_verification"] = verify_ledger(db_path)
        report["train_seed_start"] = seed
        report["holdout_seed_start"] = seed + 1000000
        db.commit()
    if core_dir:
        # The real Core may COMMENT on the scorecard, but may not alter Q or
        # grant order authority. It runs in a credential-free subprocess.
        from src.bean_core_bridge import observe_with_current_bean
        observation = {
            "study": "AIscend synthetic capital paired evaluation",
            "metrics": report["scoreboard"],
            "comparison": report["paired_mean_bean_minus_price_action_usd"],
            "q_sha256": report["q_sha256"],
            "evidence_sha256": hashlib.sha256(
                db_path.read_bytes()).hexdigest(),
            "limitations": report["limitation"],
        }
        review = observe_with_current_bean(
            core_dir, report_path.parent / "bean-capital-core.sqlite3",
            observation)
        if review["trade_authorized"] is not False:
            raise AssertionError("Core Bridge must remain advisory")
        report["bean_core_review"] = review
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="artifacts/bean-capital-evidence.sqlite3")
    parser.add_argument("--output", default="artifacts/bean-capital-report.json")
    parser.add_argument("--seed", type=int, default=26010)
    parser.add_argument("--train-seeds", type=int, default=24)
    parser.add_argument("--holdout-seeds", type=int, default=125)
    parser.add_argument("--periods", type=int, default=300)
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--fee-bps", type=float, default=60.0)
    parser.add_argument("--spread-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--bean-core", default=None)
    args = parser.parse_args()
    report = experiment(
        db_path=args.db, report_path=args.output, seed=args.seed,
        train_seeds=args.train_seeds, holdout_seeds=args.holdout_seeds,
        periods=args.periods, capital=args.capital,
        fee_bps=args.fee_bps, spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps, core_dir=args.bean_core)
    print(json.dumps({
        "scoreboard": report["scoreboard"],
        "paired_mean_bean_minus_price_action_usd":
            report["paired_mean_bean_minus_price_action_usd"],
        "ledger_verification": report["ledger_verification"],
        "research_only": True}, indent=2))


if __name__ == "__main__":
    main()
