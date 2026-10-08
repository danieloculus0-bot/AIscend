from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN
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
    can_view: bool = False
    can_trade: bool = False
    can_transfer: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "balances": [item.as_dict() for item in self.balances],
            "tradable_spot_products": self.tradable_spot_products,
            "product_ids": list(self.product_ids),
            "can_view": self.can_view,
            "can_trade": self.can_trade,
            "can_transfer": self.can_transfer,
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

    @staticmethod
    def _floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
        value = Decimal(str(value))
        increment = Decimal(str(increment))
        if value <= 0:
            raise ValueError("order size must be positive")
        if increment <= 0:
            return value
        steps = (value / increment).to_integral_value(rounding=ROUND_DOWN)
        normalized = steps * increment
        if normalized <= 0:
            raise ValueError(
                f"order size {value} is below Coinbase increment {increment}"
            )
        return normalized

    def _normalized_order_size(
        self,
        product_id: str,
        value: Decimal,
        *,
        side: str,
    ) -> Decimal:
        product = self.product(product_id)
        key = "quote_increment" if side.upper() == "BUY" else "base_increment"
        increment = Decimal(str(product.get(key) or "0"))
        normalized = self._floor_to_increment(value, increment)

        min_key = "quote_min_size" if side.upper() == "BUY" else "base_min_size"
        minimum = Decimal(str(product.get(min_key) or "0"))
        if minimum > 0 and normalized < minimum:
            raise ValueError(
                f"{product_id.upper()} {side.upper()} size {normalized} "
                f"is below Coinbase minimum {minimum}"
            )
        return normalized

    @classmethod
    def _require_order_success(
        cls,
        response: Any,
        operation: str,
    ) -> dict[str, Any]:
        payload = cls._dict(response)
        success = payload.get("success")
        success_response = payload.get("success_response") or {}
        order_id = str(
            success_response.get("order_id")
            or payload.get("order_id")
            or ""
        ).strip()

        if success is False:
            error = payload.get("error_response") or payload.get("failure_reason") or {}
            if isinstance(error, dict):
                code = str(
                    error.get("error")
                    or error.get("error_details")
                    or error.get("failure_reason")
                    or ""
                ).strip()
                message = str(
                    error.get("message")
                    or error.get("error_details")
                    or error.get("preview_failure_reason")
                    or ""
                ).strip()
                detail = ": ".join(part for part in (code, message) if part)
            else:
                detail = str(error).strip()
            raise RuntimeError(
                f"Coinbase rejected {operation}"
                + (f": {detail}" if detail else ".")
            )

        if not order_id:
            raise RuntimeError(
                f"Coinbase did not return an order id for {operation}: {payload}"
            )
        return payload

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
            post_only = bool(item.get("post_only", False))
            auction_mode = bool(item.get("auction_mode", False))
            if product_type not in {"SPOT", "UNKNOWN_PRODUCT_TYPE", ""}:
                continue
            # AdvancedSpotEngine currently executes market orders. Products that
            # are visible but still auction/limit/post/cancel-only remain visible
            # to Listing Sentinel, but are not advertised as market-order ready.
            if (
                trading_disabled
                or cancel_only
                or limit_only
                or post_only
                or auction_mode
            ):
                continue
            product_ids.append(product_id.upper())

        product_ids = sorted(set(product_ids))
        permissions = self.permissions()
        return AdvancedTradeStatus(
            configured=True,
            balances=tuple(balances),
            tradable_spot_products=len(product_ids),
            product_ids=tuple(product_ids),
            can_view=permissions["can_view"],
            can_trade=permissions["can_trade"],
            can_transfer=permissions["can_transfer"],
        )


    def permissions(self) -> dict[str, bool]:
        payload = self._dict(self.vault.client().get_api_key_permissions())
        return {
            "can_view": bool(payload.get("can_view", payload.get("view", True))),
            "can_trade": bool(payload.get("can_trade", payload.get("trade", False))),
            "can_transfer": bool(payload.get("can_transfer", payload.get("transfer", False))),
        }

    @staticmethod
    def _currency_code(item: dict[str, Any]) -> str:
        currency = item.get("currency")
        if isinstance(currency, dict):
            currency = currency.get("code") or currency.get("symbol")
        return str(currency or "").upper()

    def _app_accounts(self) -> list[dict[str, Any]]:
        payload = self.vault.client().get("/v2/accounts")
        if not isinstance(payload, dict):
            return []
        rows = payload.get("data") or payload.get("accounts") or []
        return [row for row in rows if isinstance(row, dict)]

    def app_account(self, currency: str) -> dict[str, Any]:
        target = currency.upper()
        for item in self._app_accounts():
            if self._currency_code(item) == target:
                return item
        raise RuntimeError(f"Coinbase App account for {target} was not found.")

    def receive_address(self, currency: str = "USDC", network: str = "base") -> str:
        cache_key = f"ADVANCED_{currency.upper()}_{network.upper()}_ADDRESS"
        cached = os.getenv(cache_key, "").strip()
        if cached:
            return cached

        account = self.app_account(currency)
        account_id = str(account.get("id") or "")
        if not account_id:
            raise RuntimeError(f"Coinbase App account for {currency} has no account id.")

        client = self.vault.client()
        try:
            existing = client.get(
                f"/v2/accounts/{account_id}/addresses",
                params={"limit": 25},
            )
            rows = (existing or {}).get("data") if isinstance(existing, dict) else []
            for row in rows or []:
                address = str((row or {}).get("address") or "").strip()
                row_network = str((row or {}).get("network") or "").lower()
                if address and (not row_network or network.lower() in row_network):
                    os.environ[cache_key] = address
                    return address
        except Exception:
            pass

        created = client.post(
            f"/v2/accounts/{account_id}/addresses",
            data={"name": f"AIscend {network} bridge", "network": network},
        )
        data = created.get("data") if isinstance(created, dict) else None
        address = str((data or {}).get("address") or "").strip()
        if not address:
            raise RuntimeError(
                "Coinbase did not return a deposit address. "
                "The Advanced key may need Receive/Transfer permission."
            )
        os.environ[cache_key] = address
        return address

    def send_usdc_to_address(
        self,
        to_address: str,
        amount: Decimal,
        network: str = "base",
        idem: str | None = None,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("amount must be positive")
        account = self.app_account("USDC")
        account_id = str(account.get("id") or "")
        if not account_id:
            raise RuntimeError("Coinbase USDC account has no account id.")

        payload = {
            "type": "send",
            "to": to_address,
            "amount": str(amount),
            "currency": "USDC",
            "network": network,
        }
        if idem:
            payload["idem"] = idem

        response = self.vault.client().post(
            f"/v2/accounts/{account_id}/transactions",
            data=payload,
        )
        return response if isinstance(response, dict) else self._dict(response)


    def product(self, product_id: str) -> dict[str, Any]:
        response = self.vault.client().get_product(
            product_id=product_id.upper(),
            get_tradability_status=True,
        )
        return self._dict(response)

    def preview_market_buy(self, product_id: str, quote_size: Decimal) -> dict[str, Any]:
        if quote_size <= 0:
            raise ValueError("quote_size must be positive")
        quote_size = self._normalized_order_size(
            product_id,
            quote_size,
            side="BUY",
        )
        response = self.vault.client().preview_market_order_buy(
            product_id=product_id.upper(),
            quote_size=str(quote_size),
        )
        return self._dict(response)

    def preview_market_sell(self, product_id: str, base_size: Decimal) -> dict[str, Any]:
        if base_size <= 0:
            raise ValueError("base_size must be positive")
        base_size = self._normalized_order_size(
            product_id,
            base_size,
            side="SELL",
        )
        response = self.vault.client().preview_market_order_sell(
            product_id=product_id.upper(),
            base_size=str(base_size),
        )
        return self._dict(response)

    def market_buy(self, product_id: str, quote_size: Decimal, client_order_id: str) -> dict[str, Any]:
        if quote_size <= 0:
            raise ValueError("quote_size must be positive")
        quote_size = self._normalized_order_size(
            product_id,
            quote_size,
            side="BUY",
        )
        response = self.vault.client().market_order_buy(
            client_order_id=client_order_id,
            product_id=product_id.upper(),
            quote_size=str(quote_size),
        )
        return self._require_order_success(
            response,
            f"market buy {product_id.upper()}",
        )

    def market_sell(self, product_id: str, base_size: Decimal, client_order_id: str) -> dict[str, Any]:
        if base_size <= 0:
            raise ValueError("base_size must be positive")
        base_size = self._normalized_order_size(
            product_id,
            base_size,
            side="SELL",
        )
        response = self.vault.client().market_order_sell(
            client_order_id=client_order_id,
            product_id=product_id.upper(),
            base_size=str(base_size),
        )
        return self._require_order_success(
            response,
            f"market sell {product_id.upper()}",
        )

    def order(self, order_id: str) -> dict[str, Any]:
        order_id = str(order_id or "").strip()
        if not order_id:
            raise ValueError("order_id is required")
        response = self.vault.client().get_order(order_id=order_id)
        return self._dict(response)
