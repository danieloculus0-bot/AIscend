"""Deterministic funded-execution simulation of the REAL AdvancedSpotEngine.

The exchange boundary is replaced with a controlled venue. Decision routing,
SQLite state, order intent, order submission, Coinbase-style order verification,
and audit actions execute using the production application code.
Nothing here creates an actual Coinbase REST client or submits a real order.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import tempfile
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore
from src.primary_strategy import exit_reason


class SimAudit:
    """Capture real audit calls without writing to a production ledger."""

    def __init__(self):
        self.records = []

    @staticmethod
    def provider_id(payload, *names):
        for name in names:
            if name in payload:
                return payload[name]
            if name in (payload.get("success_response") or {}):
                return payload["success_response"][name]
        return None

    def record_trade(self, **kwargs):
        self.records.append(kwargs)

    def record_event(self, **kwargs):
        self.records.append(kwargs)


class SimVenue:
    """Fake venue with Coinbase API response shapes, realistic fee/slippage."""

    def __init__(self, *, price=2.0, qty=10.0, taker_bps=60.0,
                 slippage_bps=15.0, outcome="FILLED"):
        self.price = float(price)
        self.qty = float(qty)
        self.taker_bps = float(taker_bps)
        self.slippage_bps = float(slippage_bps)
        self.outcome = outcome
        self.orders = {}
        self.submissions = 0

    def position_fills(self, product):
        return {
            "complete": True,
            "fills": [{
                "entry_id": "original-1",
                "trade_time": "2026-10-01T00:00:00Z",
                "side": "BUY", "product_id": product,
                "size": "10", "price": "2", "commission": "0.12",
            }],
        }

    def product(self, product):
        return {"base_increment": "0.00000001", "base_min_size": "0.00001"}

    def preview_market_sell(self, product, size):
        assert product == "GROVE-USDC" and Decimal(size) > 0
        return {"preview_id": "sim-only", "quote_size": str(
            float(size) * self.price)}

    def market_sell(self, product, size, client_order_id):
        if self.outcome == "REJECT":
            return {"success": False, "error_response": "simulated rejection"}
        if float(size) > self.qty + 1e-8:
            raise RuntimeError("Simulated insufficient position")
        self.submissions += 1
        order_id = f"sim-order-{self.submissions}"
        net_price = self.price * (1 - self.slippage_bps / 10000)
        fee = float(size) * net_price * self.taker_bps / 10000
        self.orders[order_id] = {
            "order_id": order_id, "status": self.outcome,
            "filled_size": str(size),
            "filled_value": str(float(size) * net_price),
            "average_filled_price": str(net_price),
            "total_fees": str(fee),
        }
        return {"success": True, "success_response": {"order_id": order_id}}

    def order_detail(self, order_id):
        return {"order": dict(self.orders[order_id])}

    def order_fills(self, order_id):
        order = self.orders[order_id]
        if order["status"] != "FILLED":
            return {"fills": []}
        return {"fills": [{
            "trade_id": order_id, "commission": order["total_fees"],
            "size": order["filled_size"],
            "price": order["average_filled_price"],
        }]}


class CashLearner:
    def assess(self, product):
        return {
            "trade_effect": "ADOPTED_LONG_CASH",
            "learned_strategy": "cash", "target_exposure": 0,
            "chosen_action_scores_bps": {"cash": 30},
            "observed_samples": 60,
        }


def snapshot(price, *, signal=None):
    return {
        "asset_universe": {"scored": [signal] if signal else []},
        "tradable_products": ["GROVE-USDC"],
        "positions": {
            "GROVE-USDC": {"qty": 10, "available_qty": 10,
                           "mark_price": price}},
        "cash_by_quote": {"USDC": .5},
        "balance_values_usd": {"GROVE": 10 * price},
        "net_liquidation": .5 + 10 * price,
        "prices": {"GROVE-USDC": price},
    }


def run_simulation(*, seed=20261022, policy_cases=5000,
                   execution_cases=300):
    rng = random.Random(seed)
    report = {"seed": seed, "policy_cases": policy_cases,
              "execution_cases": execution_cases,
              "policy_outcomes": {}, "execution_outcomes": {},
              "failures": [], "real_coinbase_orders": 0,
              "simulated_venue_orders": 0,
              "execution_path": "AdvancedSpotEngine._primary_trade_step",
              "exchange": "SimVenue replacing authenticated Coinbase"}

    # Pure decision truth-table fuzzing: no future prices used as policy inputs.
    counts = Counter()
    for idx in range(policy_cases):
        case = idx % 5
        price = rng.uniform(1.45, 1.83) if case == 0 else (
            rng.uniform(1.88, 2.25) if case == 1 else (
            rng.uniform(1.5, 2.3) if case == 2 else (
            rng.uniform(2.3, 2.8) if case == 3 else
            rng.uniform(1.5, 2.5))))
        peak = (price / rng.uniform(.85, .91)) if case == 2 else price
        cash = case == 3
        expired = case == 4
        reason = exit_reason(
            "GROVE-USDC", {"asset_universe": {"scored": []}},
            current_mark_price=price, observed_peak_price=peak,
            verified_entry_price=2.0 if case != 2 else None,
            learned_exposure=False if cash else None,
            deadline_reached=expired)
        if case == 0 and reason != "verified_entry_loss_limit":
            report["failures"].append(f"loss-not-sold:{idx}:{reason}")
        elif case == 1 and reason is not None:
            report["failures"].append(f"healthy-position-exited:{idx}:{reason}")
        elif case == 2 and reason not in {
                "observed_trailing_drawdown", "severe_observed_drawdown"}:
            report["failures"].append(f"peak-loss-not-sold:{idx}:{reason}")
        elif case == 3 and reason != "bean_q_policy_cash":
            report["failures"].append(f"learned-cash-ignored:{idx}:{reason}")
        elif case == 4 and reason not in {
                "verified_entry_loss_limit", "planned_maturity_exit_unobserved"}:
            report["failures"].append(f"maturity-not-sold:{idx}:{reason}")
        counts[reason or "HOLD"] += 1
    report["policy_outcomes"] = dict(counts)

    # Stateful venue simulation: genuine execution and fill verification methods,
    # with the real trading engine + SQLite journal. Only the venue is fake.
    counts = Counter()
    with tempfile.TemporaryDirectory() as folder:
        store = StateStore(Path(folder) / "simulation.db")
        try:
            for idx in range(execution_cases):
                case = idx % 6
                if case == 0:  # loss vs verified entry
                    price = rng.uniform(1.50, 1.83)
                elif case == 1:  # rational hold before maturity
                    price = rng.uniform(1.88, 2.20)
                elif case == 2:  # trailing peak collapse
                    price = rng.uniform(1.85, 2.15)
                elif case == 3:  # BEAN genuinely elects cash
                    price = rng.uniform(1.8, 2.1)
                elif case == 4:  # expired position with no scanner row
                    price = rng.uniform(1.88, 2.2)
                else:  # slow sustained downtrend without 8% entry loss
                    price = rng.uniform(1.88, 2.08)
                venue = SimVenue(price=price)
                engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
                engine.store = store
                engine.rail = venue
                engine.audit = SimAudit()
                engine.growth_gate = CashLearner() if case == 3 else None
                engine._taker_cost_bps = lambda: (60.0, "simulated_fee_tier")
                engine.sync = lambda: {"simulated": True}

                product = "GROVE-USDC"
                store.set_meta("pending_coinbase_order", "")
                store.set_meta("primary_peak:" + product,
                               str(price / .89) if case == 2 else str(price))
                store.set_meta("primary_maturity_at:" + product,
                               "1" if case == 4 else str(time.time() + 3600))
                store.set_meta("primary_fill_basis:" + product, "")
                store.set_meta("primary_fill_basis_checked_at:" + product, "0")
                signal = None
                if case == 5:
                    signal = {"product": product, "return_5m": -.003,
                              "return_1h": -.009, "return_6h": -.028,
                              "spread_bps": 8, "volume_ratio": 1.0}
                engine._primary_trade_step(snapshot(price, signal=signal))
                decision = store.latest_decisions(1)[0]
                sale = decision["action"] == "SELL"
                expected_sale = case != 1
                if sale != expected_sale:
                    report["failures"].append(
                        f"execution-case-{idx}: expected SELL={expected_sale}, "
                        f"got {decision['action']}/{decision['status']}")
                if sale:
                    if decision["status"] != "FILLED_VERIFIED":
                        report["failures"].append(
                            f"sale-not-verified:{idx}:{decision['status']}")
                    if venue.submissions != 1:
                        report["failures"].append(
                            f"wrong-sale-count:{idx}:{venue.submissions}")
                elif venue.submissions:
                    report["failures"].append(f"hold-traded:{idx}")
                if decision["action"] not in ("SELL", "HOLD"):
                    report["failures"].append(f"unknown-action:{idx}")
                counts[f"case{case}_{decision['action']}"] += 1
                report["simulated_venue_orders"] += venue.submissions
        finally:
            store.close()
    report["execution_outcomes"] = dict(counts)
    report["passed"] = len(report["failures"]) == 0
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--policy-cases", type=int, default=5000)
    parser.add_argument("--execution-cases", type=int, default=300)
    parser.add_argument("--output", default="artifacts/execution-simulation.json")
    args = parser.parse_args()
    result = run_simulation(
        seed=args.seed, policy_cases=args.policy_cases,
        execution_cases=args.execution_cases)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit("Execution simulation failed")


if __name__ == "__main__":
    main()
