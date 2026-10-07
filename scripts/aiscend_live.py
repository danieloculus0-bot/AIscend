from __future__ import annotations

import argparse
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import StateStore, app_data_dir  # noqa: E402
from src.live import LiveEngine  # noqa: E402
from src.wallets.cdp_wallet import CdpWallet  # noqa: E402


def make_engine(network: str) -> tuple[StateStore, LiveEngine]:
    store = StateStore(app_data_dir() / f"live-{network}.db")
    wallet = CdpWallet(network=network)
    return store, LiveEngine(store=store, wallet=wallet)


def print_snapshot(snapshot: dict) -> None:
    print(f"Network: {snapshot['network']}")
    print(f"Wallet:  {snapshot['wallet_address']}")
    print(f"USDC:    {snapshot['cash']:.6f}")
    weth_qty = snapshot["positions"].get("WETH", {}).get("qty", 0.0)
    print(f"WETH:    {weth_qty:.10f}")
    print(f"WETH px: ${snapshot['prices']['WETH']:.4f}")
    print(f"Net:     ${snapshot['net_liquidation']:.6f}")
    print(f"P/L:     ${snapshot['pnl']:+.6f}")


def run_once(engine: LiveEngine) -> None:
    before = engine.sync()
    print_snapshot(before)
    after = engine.step()
    row = engine.store.latest_decisions(1)[0]
    print()
    print(
        f"Decision: {row['action']} {row['symbol'] or ''} "
        f"{float(row['fraction']):.1%} [{row['status']}]"
    )
    print(f"Reason:   {row['rationale']}")
    print()
    print_snapshot(after)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run AIscend against the Coinbase CDP smart-account wallet."
    )
    parser.add_argument(
        "--network",
        default="base-sepolia",
        choices=("base-sepolia", "base"),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("once")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--interval", type=float, default=60.0)

    args = parser.parse_args()
    store, engine = make_engine(args.network)
    try:
        if args.command == "status":
            print_snapshot(engine.sync())
            return 0
        if args.command == "once":
            run_once(engine)
            return 0

        while True:
            print("=" * 60)
            run_once(engine)
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
