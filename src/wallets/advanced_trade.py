from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

import keyring
from coinbase.rest import RESTClient


_ADVANCED_SERVICE = "AutonomousCapitalLab/AdvancedTrade"
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
    """Coinbase Advanced keys held in OS credential storage or environment."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend if backend is not None else keyring

    def _credentials(self) -> tuple[str, str] | None:
        # Use a complete environment pair first; never mix credentials from
        # separate sources if only one environment variable was provided.
        env_pair = tuple(os.getenv(key, "").strip() for key in _ADVANCED_KEYS)
        if all(env_pair):
            return env_pair

        try:
            stored_pair = tuple(
                (self.backend.get_password(_ADVANCED_SERVICE, key) or "").strip()
                for key in _ADVANCED_KEYS
            )
        except Exception:
            return None
        return stored_pair if all(stored_pair) else None

    def configured(self) -> bool:
        return self._credentials() is not None

    def configure(self, api_key_id: str, api_key_secret: str) -> None:
        api_key_id = api_key_id.strip()
        api_key_secret = api_key_secret.strip()
        if not api_key_id or not api_key_secret:
            raise ValueError("Advanced Trade API key id and private key are required.")
        os.environ["ADVANCED_TRADE_API_KEY_ID"] = api_key_id
        os.environ["ADVANCED_TRADE_API_KEY_SECRET"] = api_key_secret

    def save(self, api_key_id: str, api_key_secret: str) -> None:
        """Persist only after the provided credentials authenticate."""
        api_key_id = api_key_id.strip()
        api_key_secret = api_key_secret.strip()
        if not api_key_id or not api_key_secret:
            raise ValueError("Advanced Trade API key id and private key are required.")
        self.backend.set_password(_ADVANCED_SERVICE, _ADVANCED_KEYS[0], api_key_id)
        self.backend.set_password(_ADVANCED_SERVICE, _ADVANCED_KEYS[1], api_key_secret)

    def clear(self) -> None:
        for key in _ADVANCED_KEYS:
            try:
                self.backend.delete_password(_ADVANCED_SERVICE, key)
            except Exception:
                pass
            os.environ.pop(key, None)

    def client(self, timeout: int = 10) -> RESTClient:
        credentials = self._credentials()
        if credentials is None:
            raise RuntimeError("Coinbase Advanced Trade is not configured.")
        return RESTClient(
            api_key=credentials[0],
            api_secret=credentials[1],
            timeout=timeout,
        )


class CoinbaseExplicitRejection(RuntimeError):
    """Exchange explicitly stated the order was not accepted."""


class CoinbaseSubmissionUnconfirmed(RuntimeError):
    """Submission outcome unknown; caller must retain the idempotency record."""


def require_accepted_order(response: dict[str, Any]) -> dict[str, Any]:
    """Separate an explicit reject from an ambiguous provider response.

    Both are failures to confirm acceptance; only an explicit reject can
    release the durable pending-order intent without reconciliation.
    """
    if isinstance(response, dict) and response.get("success") is True:
        return response
    reason = ""
    if isinstance(response, dict):
        reason = str(response.get("error_response") or response.get("failure_reason")
                     or response.get("message") or "unconfirmed order response")
    error = f"Coinbase order not confirmed accepted: {reason or 'unconfirmed response'}"
    if isinstance(response, dict) and response.get("success") is False:
        raise CoinbaseExplicitRejection(error)
    raise CoinbaseSubmissionUnconfirmed(error)


class AdvancedTradeSpot:
    """
    Coinbase Advanced spot execution rail.

    This is deliberately separate from the Base CDP smart-wallet rail. A product
    being researchable does not make it executable until it is tradable for this
    Coinbase account and the relevant quote balance is available.
    """

    def __init__(self, vault: AdvancedTradeVault | None = None) -> None:
        self.vault = vault or AdvancedTradeVault()

    @classmethod
    def validate_credentials(cls, api_key_id: str, api_key_secret: str):
        """Prove an Advanced key works before persisting it or changing env.

        The candidate client is transient and does not overwrite credentials
        belonging to a currently running trading engine.
        """
        if not api_key_id.strip() or not api_key_secret.strip():
            raise ValueError("Advanced Trade key id and secret are required.")

        class CandidateVault:
            def client(self, timeout=10):
                return RESTClient(
                    api_key=api_key_id.strip(),
                    api_secret=api_key_secret.strip(),
                    timeout=timeout,
                )

        status = cls(vault=CandidateVault()).status()
        if not status.can_view or not status.can_trade:
            raise RuntimeError(
                "Coinbase Advanced key requires both view and trade permissions.")
        return status

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

    def taker_fee_bps(self) -> float | None:
        """Read the authenticated account's current Coinbase spot fee tier."""
        data = self._dict(self.vault.client().get_transaction_summary(
            product_type="SPOT"))
        tier = data.get("fee_tier") or data.get("spot_fee_tier") or {}
        if not isinstance(tier, dict):
            tier = self._dict(tier)
        raw = tier.get("taker_fee_rate") or data.get("taker_fee_rate")
        if raw is None:
            return None
        value = Decimal(str(raw))
        if not value.is_finite() or value < 0:
            return None
        return float(value * Decimal("10000"))

    def find_order_id_by_client_id(
        self, *, product_id: str, client_order_id: str, max_pages: int = 10
    ) -> str | None:
        """Locate an uncertain submission without ever creating another order.

        Coinbase's authenticated List Orders endpoint supports pagination.
        A missing listing is NOT confirmation that a submission failed.
        """
        if not product_id or not client_order_id:
            raise ValueError("Product and client order ID are required")
        client = self.vault.client()
        cursor = None
        seen_cursors = set()
        matched_ids = set()
        for _ in range(max_pages):
            kwargs = {"product_ids": [product_id.upper()], "limit": 100}
            if cursor:
                kwargs["cursor"] = cursor
            response = self._dict(client.list_orders(**kwargs))
            orders = response.get("orders")
            if not isinstance(orders, list):
                raise RuntimeError("Coinbase order history returned invalid data")
            for order in orders:
                if not isinstance(order, dict):
                    continue
                if (str(order.get("client_order_id") or "") == client_order_id
                        and str(order.get("product_id") or "").upper()
                        == product_id.upper()):
                    found = str(order.get("order_id") or "")
                    if found:
                        matched_ids.add(found)
            if len(matched_ids) > 1:
                raise RuntimeError("Coinbase returned multiple orders for one client ID")
            next_cursor = str(response.get("cursor") or "")
            if response.get("has_next") is False or not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise RuntimeError("Coinbase order history repeated a cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return next(iter(matched_ids)) if matched_ids else None

    def order_detail(self, order_id: str) -> dict[str, Any]:
        """Read an authenticated Coinbase order (not the submit ACK)."""
        if not order_id or not order_id.strip():
            raise ValueError("Coinbase order ID is required")
        return self._dict(self.vault.client().get_order(order_id=order_id))

    def position_fills(self, product_id: str, *, max_pages: int = 30) -> dict[str, Any]:
        """Read actual Coinbase fills for this product, exhausting pagination.

        A capped or incomplete history is never treated as verified cost basis.
        """
        client = self.vault.client()
        cursor = None
        seen_cursors = set()
        fills: list[dict[str, Any]] = []
        for _ in range(max_pages):
            kwargs = {"product_ids": [product_id.upper()], "limit": 100}
            if cursor:
                kwargs["cursor"] = cursor
            response = self._dict(client.get_fills(**kwargs))
            if "fills" not in response:
                raise RuntimeError("Coinbase fill history returned no fills field.")
            rows = response["fills"]
            if not isinstance(rows, list):
                raise RuntimeError("Coinbase fill history returned invalid rows.")
            fills.extend(rows)
            next_cursor = str(response.get("cursor") or "")
            if not next_cursor:
                return {"fills": fills, "complete": True, "pages": len(seen_cursors) + 1}
            if next_cursor in seen_cursors:
                raise RuntimeError("Coinbase fill pagination repeated a cursor.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return {"fills": fills, "complete": False, "pages": max_pages}


    def order_fills(self, order_id: str) -> dict[str, Any]:
        """Read actual execution fills/commissions, never a preview estimate."""
        if not order_id or not order_id.strip():
            raise ValueError("Coinbase order ID is required")
        return self._dict(self.vault.client().get_fills(order_ids=[order_id]))

    def preview_market_sell(self, product_id: str,
                            base_size: Decimal) -> dict[str, Any]:
        if base_size <= 0:
            raise ValueError("base_size must be positive")
        preview = self._dict(self.vault.client().preview_market_order_sell(
            product_id=product_id.upper(), base_size=str(base_size)))
        if preview.get("errs") or preview.get("errors") or not preview:
            raise RuntimeError("Coinbase market sell preview rejected: "
                               + str(preview.get("errs") or preview.get("errors")))
        return preview

    def preview_market_buy(self, product_id: str, quote_size: Decimal) -> dict[str, Any]:
        if quote_size <= 0:
            raise ValueError("quote_size must be positive")
        response = self.vault.client().preview_market_order_buy(
            product_id=product_id.upper(),
            quote_size=str(quote_size),
        )
        preview = self._dict(response)
        if preview.get("errs") or preview.get("errors"):
            raise RuntimeError("Coinbase market buy preview rejected: "
                               + str(preview.get("errs") or preview.get("errors")))
        if not preview:
            raise RuntimeError("Coinbase market buy preview is missing")
        return preview

    def market_buy(self, product_id: str, quote_size: Decimal, client_order_id: str) -> dict[str, Any]:
        if quote_size <= 0:
            raise ValueError("quote_size must be positive")
        response = self.vault.client().market_order_buy(
            client_order_id=client_order_id,
            product_id=product_id.upper(),
            quote_size=str(quote_size),
        )
        return require_accepted_order(self._dict(response))

    def market_sell(self, product_id: str, base_size: Decimal, client_order_id: str) -> dict[str, Any]:
        if base_size <= 0:
            raise ValueError("base_size must be positive")
        response = self.vault.client().market_order_sell(
            client_order_id=client_order_id,
            product_id=product_id.upper(),
            base_size=str(base_size),
        )
        return require_accepted_order(self._dict(response))
