from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import keyring
from cdp import CdpClient, EncodedCall
from cdp.actions.evm.swap.types import SmartAccountSwapOptions
from web3 import Web3


SERVICE_NAME = "AutonomousCapitalLab/CDP"
OWNER_ACCOUNT_NAME = "autonomous-capital-owner"
SMART_ACCOUNT_NAME = "autonomous-capital"
DEFAULT_NETWORK = "base-sepolia"

USDC = {
    "base": {
        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "decimals": 6,
    },
    "base-sepolia": {
        "address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "decimals": 6,
    },
}
WETH = {
    "base": {
        "address": "0x4200000000000000000000000000000000000006",
        "decimals": 18,
    },
    "base-sepolia": {
        "address": "0x4200000000000000000000000000000000000006",
        "decimals": 18,
    },
}
RPC_URLS = {
    "base": "https://mainnet.base.org",
    "base-sepolia": "https://sepolia.base.org",
}
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
MAX_UINT256 = (1 << 256) - 1

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

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


@dataclass(frozen=True)
class TradingSnapshot:
    address: str
    network: str
    usdc: Decimal
    weth: Decimal
    weth_price_usdc: Decimal
    net_usdc: Decimal


@dataclass(frozen=True)
class SwapExecution:
    user_op_hash: str
    from_symbol: str
    from_amount: Decimal
    to_symbol: str
    to_amount: Decimal


class CredentialVault:
    """Stores CDP credentials in the OS credential backend, not in the repo."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend or keyring

    def configured(self) -> bool:
        if all(os.getenv(key, "").strip() for key in _CREDENTIAL_KEYS):
            return True
        try:
            return all(
                self.backend.get_password(SERVICE_NAME, key)
                for key in _CREDENTIAL_KEYS
            )
        except Exception:
            return False

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
            existing = os.getenv(key, "").strip()
            if existing:
                continue

            try:
                value = self.backend.get_password(SERVICE_NAME, key)
            except Exception:
                value = None
            if not value:
                missing.append(key)
                continue
            os.environ[key] = value

        if missing:
            raise RuntimeError(
                "CDP credentials are not configured: " + ", ".join(missing)
            )


class CdpWallet:
    """Coinbase CDP smart-account wallet for the AIscend experiment."""

    def __init__(
        self,
        vault: CredentialVault | None = None,
        account_name: str = SMART_ACCOUNT_NAME,
        owner_name: str = OWNER_ACCOUNT_NAME,
        network: str = DEFAULT_NETWORK,
        paymaster_url: str | None = None,
    ) -> None:
        if network not in USDC:
            raise ValueError(f"Unsupported network: {network}")
        self.vault = vault or CredentialVault()
        self.account_name = account_name
        self.owner_name = owner_name
        self.network = network
        self.paymaster_url = paymaster_url or os.getenv("CDP_PAYMASTER_URL") or None

    async def _smart_account(self, cdp: CdpClient):
        owner = await cdp.evm.get_or_create_account(name=self.owner_name)
        return await cdp.evm.get_or_create_smart_account(
            name=self.account_name,
            owner=owner,
        )

    async def ensure_account(self) -> str:
        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await self._smart_account(cdp)
            return str(account.address)

    async def status(self) -> WalletStatus:
        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await self._smart_account(cdp)
            balances = await self._list_balances(cdp, str(account.address))
            return WalletStatus(
                address=str(account.address),
                account_name=self.account_name,
                network=self.network,
                balances=balances,
            )

    async def _list_balances(
        self,
        cdp: CdpClient,
        address: str,
    ) -> tuple[WalletBalance, ...]:
        page = await cdp.evm.list_token_balances(
            address=address,
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
                        symbol=str(token.symbol or "UNKNOWN").upper(),
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
                address=address,
                network=self.network,
                page_size=100,
                page_token=next_token,
            )

        return tuple(balances)

    async def request_testnet_funds(self, token: str = "usdc") -> str:
        if self.network != "base-sepolia":
            raise RuntimeError("Faucet funding is restricted to Base Sepolia.")

        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await self._smart_account(cdp)
            result = await cdp.evm.request_faucet(
                address=account.address,
                network=self.network,
                token=token.lower(),
            )
            tx_hash = getattr(result, "transaction_hash", result)
            return str(tx_hash)

    async def trading_snapshot(self) -> TradingSnapshot:
        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await self._smart_account(cdp)
            address = str(account.address)
            balances = await self._list_balances(cdp, address)

            usdc = self._balance_for(balances, "USDC", USDC[self.network]["address"])
            weth = self._balance_for(balances, "WETH", WETH[self.network]["address"])

            price = await cdp.evm.get_swap_price(
                from_token=WETH[self.network]["address"],
                to_token=USDC[self.network]["address"],
                from_amount=str(10 ** WETH[self.network]["decimals"]),
                network=self.network,
                taker=address,
            )
            if not getattr(price, "liquidity_available", False):
                raise RuntimeError("No WETH/USDC swap liquidity is currently available.")

            raw_to = Decimal(str(price.to_amount))
            weth_price_usdc = raw_to / (Decimal(10) ** USDC[self.network]["decimals"])
            net = usdc + (weth * weth_price_usdc)

            return TradingSnapshot(
                address=address,
                network=self.network,
                usdc=usdc,
                weth=weth,
                weth_price_usdc=weth_price_usdc,
                net_usdc=net,
            )

    @staticmethod
    def _balance_for(
        balances: tuple[WalletBalance, ...],
        symbol: str,
        contract_address: str,
    ) -> Decimal:
        target = contract_address.lower()
        for balance in balances:
            if balance.contract_address and balance.contract_address.lower() == target:
                return balance.amount
        for balance in balances:
            if balance.symbol == symbol:
                return balance.amount
        return Decimal("0")

    async def swap_usdc_to_weth(self, fraction: float) -> SwapExecution:
        return await self._swap("USDC", "WETH", fraction)

    async def swap_weth_to_usdc(self, fraction: float) -> SwapExecution:
        return await self._swap("WETH", "USDC", fraction)

    async def _swap(
        self,
        from_symbol: str,
        to_symbol: str,
        fraction: float,
    ) -> SwapExecution:
        fraction = max(0.0, min(1.0, float(fraction)))
        if fraction <= 0:
            raise ValueError("Swap fraction must be greater than zero.")

        token_map = {"USDC": USDC[self.network], "WETH": WETH[self.network]}
        from_token = token_map[from_symbol]
        to_token = token_map[to_symbol]

        self.vault.load_into_environment()
        async with CdpClient() as cdp:
            account = await self._smart_account(cdp)
            address = str(account.address)
            before = await self._list_balances(cdp, address)
            available = self._balance_for(before, from_symbol, from_token["address"])
            atomic_available = int(available * (Decimal(10) ** from_token["decimals"]))
            atomic_amount = int(Decimal(atomic_available) * Decimal(str(fraction)))

            if atomic_amount <= 0:
                raise RuntimeError(f"No {from_symbol} balance is available to swap.")

            await self._ensure_permit2_allowance(
                account=account,
                token_address=from_token["address"],
                required_amount=atomic_amount,
            )

            options = SmartAccountSwapOptions(
                network=self.network,
                from_token=from_token["address"],
                to_token=to_token["address"],
                from_amount=str(atomic_amount),
                slippage_bps=100,
                paymaster_url=self.paymaster_url,
            )
            result = await account.swap(options)
            receipt = await account.wait_for_user_operation(
                user_op_hash=result.user_op_hash,
                timeout_seconds=120,
            )
            if str(getattr(receipt, "status", "")).lower() != "complete":
                raise RuntimeError(
                    f"Swap user operation ended with status {getattr(receipt, 'status', 'unknown')}."
                )

            after = await self._list_balances(cdp, address)
            before_from = self._balance_for(before, from_symbol, from_token["address"])
            after_from = self._balance_for(after, from_symbol, from_token["address"])
            before_to = self._balance_for(before, to_symbol, to_token["address"])
            after_to = self._balance_for(after, to_symbol, to_token["address"])

            return SwapExecution(
                user_op_hash=str(result.user_op_hash),
                from_symbol=from_symbol,
                from_amount=max(Decimal("0"), before_from - after_from),
                to_symbol=to_symbol,
                to_amount=max(Decimal("0"), after_to - before_to),
            )

    async def _ensure_permit2_allowance(
        self,
        account: Any,
        token_address: str,
        required_amount: int,
    ) -> None:
        w3 = Web3(Web3.HTTPProvider(RPC_URLS[self.network]))
        token = Web3.to_checksum_address(token_address)
        owner = Web3.to_checksum_address(str(account.address))
        permit2 = Web3.to_checksum_address(PERMIT2_ADDRESS)
        contract = w3.eth.contract(address=token, abi=ERC20_ABI)

        allowance = int(contract.functions.allowance(owner, permit2).call())
        if allowance >= required_amount:
            return

        data = contract.functions.approve(permit2, MAX_UINT256).build_transaction(
            {"from": owner, "gas": 0}
        )["data"]
        result = await account.send_user_operation(
            network=self.network,
            calls=[
                EncodedCall(
                    to=token,
                    data=data,
                    value=0,
                )
            ],
            paymaster_url=self.paymaster_url,
        )
        receipt = await account.wait_for_user_operation(
            user_op_hash=result.user_op_hash,
            timeout_seconds=120,
        )
        if str(getattr(receipt, "status", "")).lower() != "complete":
            raise RuntimeError(
                "Permit2 approval did not complete: "
                + str(getattr(receipt, "status", "unknown"))
            )
