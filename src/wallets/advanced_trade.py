from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from coinbase.rest import RESTClient


_ADVANCED_KEYS = (
    "ADVANCED_TRADE_API_KEY_ID",
    "ADVANCED_TRADE_API_KEY_SECRET",
)


@dataclass(frozen=True)
class AdvancedBalance:
    currency: str
    available: Decimal
    hold: Decimal

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["available"] = str(self.available)
        data["hold"] = str(self.hold)
        return data


@dataclass(frozen=True)
class AdvancedTradeStatus:
    configured: bool
    balances: tuple[AdvancedBalance, ...]
    tradable_spot_products: int
    product_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "balances": [item.as_dict() for item in self.balances],
            "tradable_spot_products": self.tradable_spot_products,
            "product_ids": list(self.product_ids),
        }


class AdvancedTradeVault:
    """Process-local Coinbase Advanced credentials for the browser runtime."""

    def configured(self) -> bool:
        return all(os.getenv(key, "").strip() for key in _ADVANCED_KEYS)

    def configure(self, api_key_id: str, api_key_secret: str) -> None:
        api_key_id = api_key_id.strip()
        api_key_secret = api_key_secret.strip()
        if not api_key_id or not api_key_secret:
            raise ValueError("Advanced Trade API key id and private key are required.")
        os.environ["ADVANCED_TRADE_API_KEY_ID"] = api_key_id
        os.environ["ADVANCED_TRADE_API_KEY_SECRET"] = api_key_secret

    def client(self, timeout: int = 10) -> RESTClient:
        if not self.configured():
            raise RuntimeError("Coinbase Advanced Trade is not configured.")
        return RESTClient(
            api_key=os.environ["ADVANCED_TRADE_API_KEY_ID"],
            api_secret=os.environ["ADVANCED_TRADE_API_KEY_SECRET"],
            timeout=timeout,
        )


class AdvancedTradeSpot:
    """
    Coinbase Advanced spot execution rail.

    This is deliberately separate from the Base CDP smart-wallet rail. A product
    being researchable does not make it executable until it is tradable for this
    Coinbase account and the relevant quote balance is available.
    """

    def __init__(self, vault: AdvancedTradeVault | None = None) -> None:
        self.vault = vault or AdvancedTradeVault()

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        method = getattr(value, "to_dict", None)
        if callable(method):
            return method()
        return {}

    def status(self) -> AdvancedTradeStatus:
        client = self.vault.client()
        accounts_raw = self._dict(client.get_accounts())
        products_raw = self._dict(
            client.get_products(
                get_all_products=True,
                get_tradability_status=True,
            )
        )

        balances: list[AdvancedBalance] = []
        for item in accounts_raw.get("accounts") or []:
            available = item.get("available_balance") or {}
            hold = item.get("hold") or {}
            currency = str(
                item.get("currency")
                or available.get("currency")
                or hold.get("currency")
                or ""
            ).upper()
            if not currency:
                continue
            available_value = Decimal(str(available.get("value") or "0"))
            hold_value = Decimal(str(hold.get("value") or "0"))
            if available_value == 0 and hold_value == 0:
                continue
            balances.append(
                AdvancedBalance(
                    currency=currency,
                    available=available_value,
                    hold=hold_value,
                )
            )

        product_ids: list[str] = []
        for item in products_raw.get("products") or []:
            product_id = str(item.get("product_id") or item.get("id") or "")
            if not product_id:
                continue
            product_type = str(item.get("product_type") or "").upper()
            trading_disabled = bool(item.get("trading_disabled", False))
            cancel_only = bool(item.get("cancel_only", False))
            limit_only = bool(item.get("limit_only", False))
            if product_type not in {"SPOT", "UNKNOWN_PRODUCT_TYPE", ""}:
                continue
            if trading_disabled or cancel_only:
                continue
            # limit_only is still tradable, just not by market order. Keep it in
            # the account universe, while execution code checks the order type.
            product_ids.append(product_id.upper())

        product_ids = sorted(set(product_ids))
        return AdvancedTradeStatus(
            configured=True,
            balances=tuple(balances),
            tradable_spot_products=len(product_ids),
            product_ids=tuple(product_ids),
        )

    def preview_market_buy(self, product_id: str, quote_size: Decimal) -> dict[str, Any]:
        if quote_size <= 0:
            raise ValueError("quote_size must be positive")
        response = self.vault.client().preview_market_order_buy(
            product_id=product_id.upper(),
            quote_size=str(quote_size),
        )
        return self._dict(response)

    def market_buy(self, product_id: str, quote_size: Decimal, client_order_id: str) -> dict[str, Any]:
        if quote_size <= 0:
            raise ValueError("quote_size must be positive")
        response = self.vault.client().market_order_buy(
            client_order_id=client_order_id,
            product_id=product_id.upper(),
            quote_size=str(quote_size),
        )
        return self._dict(response)

    def market_sell(self, product_id: str, base_size: Decimal, client_order_id: str) -> dict[str, Any]:
        if base_size <= 0:
            raise ValueError("base_size must be positive")
        response = self.vault.client().market_order_sell(
            client_order_id=client_order_id,
            product_id=product_id.upper(),
            base_size=str(base_size),
        )
        return self._dict(response)
