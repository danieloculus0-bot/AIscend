from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wallets.cdp_wallet import CdpWallet, CredentialVault  # noqa: E402


def configure(vault: CredentialVault, network: str) -> None:
    print("Coinbase CDP wallet configuration")
    print("Credentials will be stored in Windows Credential Manager.")
    print("They will not be written to this repository or the experiment database.")
    print()

    api_key_id = input("CDP API key ID: ").strip()
    api_key_secret = getpass.getpass("CDP API key secret: ").strip()
    wallet_secret = getpass.getpass("CDP wallet secret: ").strip()

    vault.save(api_key_id, api_key_secret, wallet_secret)
    print()
    print("Credentials saved. Verifying CDP access...")

    wallet = CdpWallet(vault=vault, network=network)
    address = asyncio.run(wallet.ensure_account())
    print(f"Connected wallet: {address}")
    print(f"Network for status/funding: {network}")


def show_status(vault: CredentialVault, network: str) -> None:
    wallet = CdpWallet(vault=vault, network=network)
    status = asyncio.run(wallet.status())

    print(f"Account: {status.account_name}")
    print(f"Address: {status.address}")
    print(f"Network: {status.network}")
    print()

    if not status.balances:
        print("No token balances found.")
        return

    print("Balances:")
    for balance in sorted(status.balances, key=lambda x: x.symbol):
        print(f"  {balance.symbol}: {balance.amount}")


def faucet(vault: CredentialVault, network: str, token: str) -> None:
    wallet = CdpWallet(vault=vault, network=network)
    tx_hash = asyncio.run(wallet.request_testnet_funds(token=token))
    print(f"Requested {token.upper()} testnet funds.")
    print(f"Transaction: {tx_hash}")


def clear(vault: CredentialVault) -> None:
    vault.clear()
    print("CDP credentials removed from the OS credential store.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Configure and inspect the Autonomous Capital CDP wallet."
    )
    parser.add_argument(
        "--network",
        default="base-sepolia",
        choices=("base-sepolia", "base"),
        help="Wallet network for status calls. Defaults to Base Sepolia.",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("configure", help="Store CDP credentials and create/retrieve the wallet.")
    sub.add_parser("status", help="Show wallet address and token balances.")

    faucet_parser = sub.add_parser("faucet", help="Request Base Sepolia test funds.")
    faucet_parser.add_argument(
        "--token",
        default="usdc",
        choices=("usdc", "eth"),
        help="Test token to request.",
    )

    sub.add_parser("clear", help="Remove stored CDP credentials.")

    args = parser.parse_args()
    vault = CredentialVault()

    try:
        if args.command == "configure":
            configure(vault, args.network)
        elif args.command == "status":
            show_status(vault, args.network)
        elif args.command == "faucet":
            faucet(vault, args.network, args.token)
        elif args.command == "clear":
            clear(vault)
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
