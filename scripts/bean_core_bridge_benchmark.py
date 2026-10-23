"""Paired, SYNTHETIC, fee-net BEAN Core bridge learning benchmark.

This harness constructs research-only paper observations. Training outcomes are
sealed before the test and are not supplied to BEAN as test inputs. Each test
choice is committed in SQLite BEFORE its simulated future paper fill is scored.
It never calls a Coinbase API or establishes real market profitability.

The comparator is a fixed, untrained REAL BEAN Core policy using the same
learn_from_paper bridge, not a CashLearner fake or a trading algorithm invented
by this test. Real-time elapsed market observation is NOT simulated here.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from src.bean_core_paper_learning import learn_from_paper
from src.bean_five_minute import BAR
from src.forward_paper import HORIZONS, _db, freeze

COST_FEE_BPS = 60
COST_SPREAD_BPS = 10
PRODUCTS = ("BTC-USD", "ETH-USD")
POLICY = "trend"
TRAIN_SAMPLES = 60


def fee_net(entry, exit_price):
    cost = (COST_FEE_BPS + COST_SPREAD_BPS / 2) / 10000
    return (exit_price / entry) * (1 - cost) ** 2 - 1


def add_sample(db_path, *, product, signal, scored_price=None):
    """Insert causally timed paper evidence, or a pre-entry pending proposal."""
    observation = signal + BAR + 1
    entry = signal + 2 * BAR
    end = entry + HORIZONS[POLICY] * BAR
    with closing(sqlite3.connect(db_path)) as db:
        _db(db)
        identity = freeze(db, product=product, policy=POLICY,
                          now=observation, fee_bps=COST_FEE_BPS,
                          spread_bps=COST_SPREAD_BPS)
        if scored_price is None:
            db.execute(
                "INSERT INTO paper_decisions "
                "(product,policy_sha,signal_ts,observed_at,entry_ts,exit_ts,state) "
                "VALUES(?,?,?,?,?,?,'PENDING')",
                (product, identity, signal, observation, entry, end))
        else:
            db.execute(
                "INSERT INTO paper_decisions "
                "(product,policy_sha,signal_ts,observed_at,entry_ts,exit_ts,"
                "state,entry_price,exit_price,net_return,reason) "
                "VALUES(?,?,?,?,?,?,'SCORED',?,?,?,'synthetic_fixture_only')",
                (product, identity, signal, observation, entry, end,
                 100.0, float(scored_price), fee_net(100.0, scored_price)))
        db.commit()
    return observation, entry, end


def committed_choice(*, core_dir, paper, learner, observed, entry):
    """Call the actual credential-free BEAN bridge before any outcome exists."""
    result = learn_from_paper(
        core_dir=core_dir, paper_db=paper, learning_db=learner,
        clock=lambda: observed + 1)
    if (result["shadow_choices_committed_before_entry"] != 1
            or result["shadow_candidate_count"] != 1
            or not result["paper_only"]
            or result["execution_authority"] != "none"):
        raise AssertionError("Real BEAN pre-entry shadow choice was not committed")
    with closing(sqlite3.connect(paper)) as db:
        row = db.execute(
            "SELECT action,observed_at FROM bean_paper_shadow "
            "ORDER BY signal_ts DESC LIMIT 1").fetchone()
    if not row or row[0] not in ("trade", "cash") or not observed < row[1] < entry:
        raise AssertionError("Invalid BEAN pre-entry decision timestamp")
    return row[0], result


def score_later(paper, *, signal, price):
    """Outcome appears in the learner's ledger only AFTER shadow decisions."""
    with closing(sqlite3.connect(paper)) as db:
        row = db.execute(
            "UPDATE paper_decisions SET state='SCORED',entry_price=100,"
            "exit_price=?,net_return=?,reason='synthetic_future_fixture' "
            "WHERE signal_ts=? AND state='PENDING'",
            (float(price), fee_net(100.0, price), signal))
        if row.rowcount != 1:
            raise AssertionError("Paper outcome missing or prematurely scored")
        db.commit()


def run_case(core_dir, *, regime, opportunities=40):
    if opportunities < 30 or opportunities % 2:
        raise ValueError("Holdout count must be even and at least 30")
    with tempfile.TemporaryDirectory(prefix="bean-bridge-benchmark-") as tmp:
        root = Path(tmp)
        learned_paper = root / "trained-paper.sqlite3"
        learned_q = root / "trained-bean.sqlite3"
        baseline_paper = root / "untrained-paper.sqlite3"
        baseline_q = root / "untrained-bean.sqlite3"

        # Exactly 60 prior scored, fee-net PAPER results. No holdout samples
        # are included in this first BEAN learning call.
        for i in range(TRAIN_SAMPLES):
            product = PRODUCTS[i % 2]
            price = 98.0 if product == PRODUCTS[0] else 103.0
            add_sample(learned_paper, product=product,
                       signal=(1000 + i * 90) * BAR, scored_price=price)
        initial = learn_from_paper(
            core_dir=core_dir, paper_db=learned_paper, learning_db=learned_q,
            clock=lambda: (6500 * BAR))
        if (initial["q_updates"] != TRAIN_SAMPLES
                or not initial["durable_checkpoint"]
                or initial["durable_q_updates"] != TRAIN_SAMPLES
                or initial["improvement_independently_verified"]):
            raise AssertionError("BEAN did not retain 60 real-core training updates")

        decisions = []
        equity = {"trained": 1.0, "untrained": 1.0, "all_cash": 1.0}
        action_counts = {"trained": {"trade": 0, "cash": 0},
                         "untrained": {"trade": 0, "cash": 0}}
        for i in range(opportunities):
            product = PRODUCTS[i % 2]
            # The reversal case deliberately invalidates the training regime.
            if regime == "stable":
                exit_price = 98.0 if product == PRODUCTS[0] else 103.0
            elif regime == "reversed":
                exit_price = 103.0 if product == PRODUCTS[0] else 98.0
            else:
                raise ValueError("Unknown synthetic regime")

            signal = (7000 + i * 90) * BAR
            observed, entry, _ = add_sample(
                learned_paper, product=product, signal=signal)
            b_observed, b_entry, _ = add_sample(
                baseline_paper, product=product, signal=signal)
            if (observed, entry) != (b_observed, b_entry):
                raise AssertionError("Comparator timing mismatch")

            # Both arms use the ACTUAL BEAN Core bridge. The baseline never
            # receives previous outcomes, so it remains an untrained control.
            baseline_action, baseline_status = committed_choice(
                core_dir=core_dir, paper=baseline_paper, learner=baseline_q,
                observed=observed, entry=entry)
            trained_action, trained_status = committed_choice(
                core_dir=core_dir, paper=learned_paper, learner=learned_q,
                observed=observed, entry=entry)
            if baseline_status["q_updates"] != 0:
                raise AssertionError("Untrained BEAN control unexpectedly learned")

            # Only now is the future price placed in the trained paper ledger.
            score_later(learned_paper, signal=signal, price=exit_price)
            realized = fee_net(100.0, exit_price)
            for arm, action in (("untrained", baseline_action),
                                ("trained", trained_action)):
                action_counts[arm][action] += 1
                equity[arm] *= 1 + (realized if action == "trade" else 0.0)
            decisions.append({
                "sample": i, "product": product,
                "signal_ts": signal, "entry_ts": entry,
                "untrained_before_entry": baseline_action,
                "trained_before_entry": trained_action,
                "synthetic_fee_net_trade_return": round(realized, 10),
            })

        # Ingest final scored result; verify checkpoint still covers original
        # evidence and new outcomes without retroactively editing decisions.
        final = learn_from_paper(
            core_dir=core_dir, paper_db=learned_paper, learning_db=learned_q,
            clock=lambda: (7000 + opportunities * 90 + 100) * BAR)
        if (final["q_updates"] != TRAIN_SAMPLES + opportunities
                or not final["durable_checkpoint"]
                or final["improvement_independently_verified"]):
            raise AssertionError("Trained core did not learn scored heldout outcomes")

        results = {
            "regime": regime,
            "train_scored_fixtures": TRAIN_SAMPLES,
            "holdout_future_price_fixtures": opportunities,
            "fee_bps_each_side": COST_FEE_BPS,
            "spread_bps_roundtrip_parameter": COST_SPREAD_BPS,
            "trained_compounded_net_pct": round(100 * (equity["trained"] - 1), 6),
            "untrained_compounded_net_pct": round(100 * (equity["untrained"] - 1), 6),
            "always_cash_compounded_net_pct": 0.0,
            "trained_vs_untrained_percentage_points": round(
                100 * (equity["trained"] - equity["untrained"]), 6),
            "actions": action_counts,
            "trained_q_updates_after": final["q_updates"],
            "checkpoint_durable": final["durable_checkpoint"],
            "holdout_decision_sha256": hashlib.sha256(json.dumps(
                decisions, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "prospective_elapsed_market_test": False,
            "live_coinbase_verified": False,
            "out_of_sample_real_profitability_proven": False,
            "synthetic_only": True,
        }
        if regime == "stable":
            if (not results["trained_compounded_net_pct"] >
                    results["untrained_compounded_net_pct"]
                    or action_counts["trained"]["cash"] < opportunities // 3
                    or action_counts["trained"]["trade"] < opportunities // 3):
                raise AssertionError(
                    "Real BEAN bridge failed to learn the stable synthetic regime")
        return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True,
                        help="Separate real BEAN repository checkout")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--holdout", type=int, default=40)
    args = parser.parse_args(argv)
    stable = run_case(args.core, regime="stable", opportunities=args.holdout)
    reversed_case = run_case(args.core, regime="reversed",
                             opportunities=args.holdout)
    report = {"schema": "aiscend.bean-core-bridge-benchmark.v1",
              "title": "Actual BEAN Core on paired synthetic paper cases",
              "source": "deterministic_fabricated_prices_NOT_live_market",
              "execution_authorized": False,
              "profitability_verified": False,
              "cases": [stable, reversed_case]}
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    main()
