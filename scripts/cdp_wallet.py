from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wallets.cdp_wallet import CdpWallet, CredentialVault  # noqa: E402


def read_key_file(path: str) -> tuple[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    key_id = str(data.get("id") or data.get("name") or "").strip()
    private_key = str(data.get("privateKey") or data.get("private_key") or "").strip()
    if not key_id or not private_key:
        raise ValueError("The CDP key JSON must contain id and privateKey.")
    return key_id, private_key


def configure(vault: CredentialVault, network: str, key_file: str | None) -> None:
    print("Coinbase CDP wallet configuration")
    print("Credentials will be stored in Windows Credential Manager.")
    print()

    if key_file:
        api_key_id, api_key_secret = read_key_file(key_file)
        print(f"Loaded CDP API key: {api_key_id}")
    else:
        api_key_id = input("CDP API key ID: ").strip()
        api_key_secret = getpass.getpass("CDP API key secret: ").strip()

    wallet_secret = getpass.getpass("CDP wallet secret: ").strip()

    vault.save(api_key_id, api_key_secret, wallet_secret)
    print()
    print("Credentials saved. Creating/retrieving AIscend smart account...")

    wallet = CdpWallet(vault=vault, network=network)
    address = asyncio.run(wallet.ensure_account())
    print(f"Smart account: {address}")
    print(f"Network: {network}")


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
        description="Configure and inspect the AIscend CDP smart-account wallet."
    )
    parser.add_argument(
        "--network",
        default="base-sepolia",
        choices=("base-sepolia", "base"),
        help="Wallet network. Defaults to Base Sepolia.",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    config_parser = sub.add_parser(
        "configure",
        help="Store CDP credentials and create/retrieve the smart account.",
    )
    config_parser.add_argument(
        "--key-file",
        help="Optional downloaded CDP API key JSON. Wallet secret is still prompted.",
    )

    sub.add_parser("status", help="Show smart-account address and token balances.")

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
            configure(vault, args.network, args.key_file)
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
