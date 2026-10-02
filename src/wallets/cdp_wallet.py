from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import keyring
from cdp import CdpClient


SERVICE_NAME = "AutonomousCapitalLab/CDP"
DEFAULT_ACCOUNT_NAME = "autonomous-capital"
DEFAULT_NETWORK = "base-sepolia"

_CREDENTIAL_KEYS = (
    "CDP_API_KEY_ID",
    "CDP_API_KEY_SECRET",
    "CDP_WALLET_SECRET",
)


@dataclass(frozen=True)
class WalletBalance:
    symbol: str
    amount: Decimal
    decimals: int
    contract_address: str | None


@dataclass(frozen=True)
class WalletStatus:
    address: str
    account_name: str
    network: str
    balances: tuple[WalletBalance, ...]


class CredentialVault:
    """Stores CDP credentials in the OS credential backend, not in the repo."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend or keyring

    def configured(self) -> bool:
        return all(self.backend.get_password(SERVICE_NAME, key) for key in _CREDENTIAL_KEYS)

    def save(self, api_key_id: str, api_key_secret: str, wallet_secret: str) -> None:
        values = {
            "CDP_API_KEY_ID": api_key_id.strip(),
            "CDP_API_KEY_SECRET": api_key_secret.strip(),
            "CDP_WALLET_SECRET": wallet_secret.strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"Missing credential values: {', '.join(missing)}")

        for key, value in values.items():
            self.backend.set_password(SERVICE_NAME, key, value)

    def clear(self) -> None:
        for key in _CREDENTIAL_KEYS:
            try:
                self.backend.delete_password(SERVICE_NAME, key)
            except Exception:
                pass

    def load_into_environment(self) -> None:
        missing: list[str] = []
        for key in _CREDENTIAL_KEYS:
            value = self.backend.get_password(SERVICE_NAME, key)
            if not value:
                missing.append(key)
                continue
            os.environ[key] = value

        if missing:
            raise RuntimeError(
                "CDP credentials are not configured in Windows Credential Manager: "
                + ", ".join(missing)
            )


class CdpWallet:
    """Minimal non-custodial CDP wallet adapter.

    This layer intentionally stops at wallet identity, balances, and testnet funding.
    Autonomous mainnet trading is added through a separate execution adapter so
    wallet custody and trading policy stay distinct.
    """

    def __init__(
        self,
        vault: CredentialVault | None = None,
        account_name: str = DEFAULT_ACCOUNT_NAME,
        network: str = DEFAULT_NETWORK,
    ) -> None:
        self.vault = vault or CredentialVault()
        self.account_name = account_name
        self.network = network

    async def ensure_account(self) -> str:
        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await cdp.evm.get_or_create_account(name=self.account_name)
            return str(account.address)

    async def status(self) -> WalletStatus:
        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await cdp.evm.get_or_create_account(name=self.account_name)
            page = await cdp.evm.list_token_balances(
                address=account.address,
                network=self.network,
                page_size=100,
            )

            balances: list[WalletBalance] = []
            while True:
                for item in page.balances:
                    token = item.token
                    amount = item.amount
                    raw = Decimal(str(amount.amount))
                    decimals = int(amount.decimals)
                    human = raw / (Decimal(10) ** decimals)
                    balances.append(
                        WalletBalance(
                            symbol=str(token.symbol or "UNKNOWN"),
                            amount=human,
                            decimals=decimals,
                            contract_address=(
                                str(token.contract_address)
                                if getattr(token, "contract_address", None)
                                else None
                            ),
                        )
                    )

                next_token = getattr(page, "next_page_token", None)
                if not next_token:
                    break

                page = await cdp.evm.list_token_balances(
                    address=account.address,
                    network=self.network,
                    page_size=100,
                    page_token=next_token,
                )

            return WalletStatus(
                address=str(account.address),
                account_name=self.account_name,
                network=self.network,
                balances=tuple(balances),
            )

    async def request_testnet_funds(self, token: str = "usdc") -> str:
        if self.network != "base-sepolia":
            raise RuntimeError("Faucet funding is restricted to Base Sepolia.")

        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await cdp.evm.get_or_create_account(name=self.account_name)
            result = await cdp.evm.request_faucet(
                address=account.address,
                network=self.network,
                token=token.lower(),
            )
            tx_hash = getattr(result, "transaction_hash", result)
            return str(tx_hash)
