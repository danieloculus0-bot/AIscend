import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from src.wallets.advanced_trade import (AdvancedTradeSpot, AdvancedTradeVault, _ADVANCED_SERVICE,
    CoinbaseExplicitRejection, CoinbaseSubmissionUnconfirmed)


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[(service, name)] = value

    def delete_password(self, service, name):
        self.values.pop((service, name), None)


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class FakeClient:
    def __init__(self):
        self.posts = []

    def get_api_key_permissions(self):
        return FakeResponse({"can_view": True, "can_trade": True, "can_transfer": True})

    def get(self, path, params=None, **kwargs):
        if path == "/v2/accounts":
            return {
                "data": [
                    {"id": "usdc-account", "currency": {"code": "USDC"}},
                ]
            }
        if path == "/v2/accounts/usdc-account/addresses":
            return {"data": []}
        raise AssertionError(path)

    def post(self, path, data=None, **kwargs):
        self.posts.append((path, data))
        if path == "/v2/accounts/usdc-account/addresses":
            return {
                "data": {
                    "address": "0x1111111111111111111111111111111111111111",
                    "network": "base",
                }
            }
        if path == "/v2/accounts/usdc-account/transactions":
            return {"data": {"id": "tx-1", "status": "pending"}}
        raise AssertionError(path)

    def get_accounts(self):
        return FakeResponse(
            {
                "accounts": [
                    {
                        "currency": "USDC",
                        "available_balance": {"value": "25.00", "currency": "USDC"},
                        "hold": {"value": "0", "currency": "USDC"},
                    },
                    {
                        "currency": "BTC",
                        "available_balance": {"value": "0", "currency": "BTC"},
                        "hold": {"value": "0", "currency": "BTC"},
                    },
                ]
            }
        )

    def get_products(self, **kwargs):
        return FakeResponse(
            {
                "products": [
                    {
                        "product_id": "BTC-USD",
                        "product_type": "SPOT",
                        "trading_disabled": False,
                        "cancel_only": False,
                        "limit_only": False,
                    },
                    {
                        "product_id": "ETH-USDC",
                        "product_type": "SPOT",
                        "trading_disabled": False,
                        "cancel_only": False,
                        "limit_only": False,
                    },
                    {
                        "product_id": "BAD-USD",
                        "product_type": "SPOT",
                        "trading_disabled": True,
                        "cancel_only": False,
                        "limit_only": False,
                    },
                    {
                        "product_id": "EARLY-USD",
                        "product_type": "SPOT",
                        "trading_disabled": False,
                        "cancel_only": False,
                        "limit_only": True,
                        "post_only": False,
                        "auction_mode": False,
                    },
                ]
            }
        )

    def preview_market_order_buy(self, **kwargs):
        return FakeResponse({"preview_id": "p1", **kwargs})

    def market_order_buy(self, **kwargs):
        return FakeResponse({"success": True, **kwargs})

    def market_order_sell(self, **kwargs):
        return FakeResponse({"success": True, **kwargs})


class FakeVault(AdvancedTradeVault):
    def __init__(self):
        self.fake_client = FakeClient()

    def configured(self):
        return True

    def client(self, timeout=10):
        return self.fake_client


class AdvancedTradeTests(unittest.TestCase):
    def test_os_keyring_survives_fresh_vault_without_environment(self):
        backend = MemoryKeyring()
        with patch.dict(os.environ, {}, clear=True):
            first = AdvancedTradeVault(backend=backend)
            self.assertFalse(first.configured())
            first.configure("coinbase-id", "coinbase-private-key")
            first.save("coinbase-id", "coinbase-private-key")
            self.assertEqual(
                backend.get_password(_ADVANCED_SERVICE, "ADVANCED_TRADE_API_KEY_ID"),
                "coinbase-id",
            )
            os.environ.pop("ADVANCED_TRADE_API_KEY_ID", None)
            os.environ.pop("ADVANCED_TRADE_API_KEY_SECRET", None)
            restarted = AdvancedTradeVault(backend=backend)
            self.assertTrue(restarted.configured())
            with patch("src.wallets.advanced_trade.RESTClient") as client:
                restarted.client()
                client.assert_called_once_with(
                    api_key="coinbase-id",
                    api_secret="coinbase-private-key",
                    timeout=10,
                )
            restarted.clear()
            self.assertFalse(restarted.configured())

    def test_incomplete_saved_pair_does_not_count_as_configured(self):
        backend = MemoryKeyring()
        backend.set_password(_ADVANCED_SERVICE, "ADVANCED_TRADE_API_KEY_ID", "id")
        with patch.dict(os.environ, {}, clear=True):
            vault = AdvancedTradeVault(backend=backend)
            self.assertFalse(vault.configured())
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                vault.client()

    def test_environment_pair_overrides_saved_pair(self):
        backend = MemoryKeyring()
        with patch.dict(os.environ, {}, clear=True):
            vault = AdvancedTradeVault(backend=backend)
            vault.save("old-id", "old-secret")
            vault.configure("new-id", "new-secret")
            with patch("src.wallets.advanced_trade.RESTClient") as client:
                vault.client()
                client.assert_called_once_with(
                    api_key="new-id", api_secret="new-secret", timeout=10
                )

    def test_candidate_key_validation_does_not_poison_environment(self):
        client = FakeClient()
        with patch.dict(os.environ, {}, clear=True), patch(
            "src.wallets.advanced_trade.RESTClient", return_value=client
        ) as factory:
            status = AdvancedTradeSpot.validate_credentials(
                "candidate-id", "candidate-secret")
            self.assertTrue(status.can_trade)
            self.assertEqual(factory.call_count, 2)
            for args in factory.call_args_list:
                self.assertEqual(args.kwargs, {
                    "api_key": "candidate-id",
                    "api_secret": "candidate-secret", "timeout": 10})
            self.assertNotIn("ADVANCED_TRADE_API_KEY_ID", os.environ)
            self.assertNotIn("ADVANCED_TRADE_API_KEY_SECRET", os.environ)

    def test_candidate_without_trade_permission_rejected(self):
        class ViewOnly(FakeClient):
            def get_api_key_permissions(self):
                return FakeResponse({"can_view": True, "can_trade": False,
                                     "can_transfer": False})
        with patch("src.wallets.advanced_trade.RESTClient",
                   return_value=ViewOnly()), patch.dict(
                       os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "view and trade"):
                AdvancedTradeSpot.validate_credentials("id", "secret")
            self.assertNotIn("ADVANCED_TRADE_API_KEY_ID", os.environ)

    def test_status_lists_balances_and_tradable_spot_products(self):
        status = AdvancedTradeSpot(FakeVault()).status()
        self.assertEqual(status.tradable_spot_products, 2)
        self.assertEqual(set(status.product_ids), {"BTC-USD", "ETH-USDC"})
        self.assertEqual(status.balances[0].currency, "USDC")
        self.assertEqual(status.balances[0].available, Decimal("25.00"))
        self.assertTrue(status.can_trade)
        self.assertTrue(status.can_transfer)

    def test_rejected_order_is_not_reported_as_executed(self):
        rail = AdvancedTradeSpot(FakeVault())
        rail.vault.fake_client.market_order_buy = lambda **kwargs: FakeResponse(
            {"success": False, "error_response": {"error": "INSUFFICIENT_FUND"}})
        with self.assertRaisesRegex(RuntimeError, "not confirmed"):
            rail.market_buy("eth-usdc", Decimal("5"), "rejected-buy")

        rail.vault.fake_client.market_order_sell = lambda **kwargs: FakeResponse(
            {"success": False, "error_response": {"error": "INVALID_SIZE"}})
        with self.assertRaisesRegex(RuntimeError, "not confirmed"):
            rail.market_sell("eth-usdc", Decimal("1"), "rejected-sell")

    def test_missing_confirmation_is_not_treated_as_a_fill(self):
        rail = AdvancedTradeSpot(FakeVault())
        rail.vault.fake_client.market_order_buy = lambda **kwargs: FakeResponse(
            {"error": "unknown response"})
        with self.assertRaisesRegex(RuntimeError, "not confirmed"):
            rail.market_buy("eth-usdc", Decimal("5"), "ambiguous")

    def test_explicit_venue_rejection_is_distinguishable(self):
        rail = AdvancedTradeSpot(FakeVault())
        rail.vault.fake_client.market_order_sell = lambda **kw: FakeResponse(
            {"success": False, "error_response": "NO_FUNDS"})
        with self.assertRaises(CoinbaseExplicitRejection):
            rail.market_sell("btc-usd", Decimal("1"), "reject-1")

    def test_ambiguous_venue_response_does_not_claim_rejection(self):
        rail = AdvancedTradeSpot(FakeVault())
        rail.vault.fake_client.market_order_sell = lambda **kw: FakeResponse(
            {"message": "gateway uncertain"})
        with self.assertRaises(CoinbaseSubmissionUnconfirmed):
            rail.market_sell("btc-usd", Decimal("1"), "unknown-1")
        rail.vault.fake_client.market_order_buy = lambda **kw: FakeResponse(
            {"message": "gateway uncertain"})
        with self.assertRaises(CoinbaseSubmissionUnconfirmed):
            rail.market_buy("btc-usd", Decimal("5"), "unknown-2")

    def test_client_order_recovery_matches_exact_authenticated_identifier(self):
        rail = AdvancedTradeSpot(FakeVault())
        calls = []
        def listing(**kw):
            calls.append(kw)
            if "cursor" not in kw:
                return FakeResponse({
                    "orders": [{"order_id": "wrong", "product_id": "GROVE-USDC",
                                "client_order_id": "someone-else"}],
                    "cursor": "next", "has_next": True,
                })
            return FakeResponse({
                "orders": [{"order_id": "coinbase-actual-id",
                            "product_id": "GROVE-USDC",
                            "client_order_id": "aiscend-same-id"}],
                "has_next": False,
            })
        rail.vault.fake_client.list_orders = listing
        found = rail.find_order_id_by_client_id(
            product_id="GROVE-USDC", client_order_id="aiscend-same-id")
        self.assertEqual(found, "coinbase-actual-id")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["product_ids"], ["GROVE-USDC"])
        self.assertEqual(calls[1]["cursor"], "next")

    def test_client_order_not_found_is_not_called_rejected(self):
        rail = AdvancedTradeSpot(FakeVault())
        rail.vault.fake_client.list_orders = lambda **kw: FakeResponse({
            "orders": [], "has_next": False})
        self.assertIsNone(rail.find_order_id_by_client_id(
            product_id="GROVE-USDC", client_order_id="aiscend-unknown"))

    def test_preview_rejection_blocks_order(self):
        rail = AdvancedTradeSpot(FakeVault())
        rail.vault.fake_client.preview_market_order_buy = lambda **kwargs: FakeResponse(
            {"errs": ["PREVIEW_INVALID"]})
        with self.assertRaisesRegex(RuntimeError, "preview rejected"):
            rail.preview_market_buy("eth-usdc", Decimal("5"))

    def test_bridge_address_and_send_wrappers(self):
        vault = FakeVault()
        rail = AdvancedTradeSpot(vault)
        os.environ.pop("ADVANCED_USDC_BASE_ADDRESS", None)
        address = rail.receive_address("USDC", "base")
        self.assertEqual(address, "0x1111111111111111111111111111111111111111")
        sent = rail.send_usdc_to_address(
            "0x2222222222222222222222222222222222222222",
            Decimal("5"),
            network="base",
            idem="bridge-1",
        )
        self.assertEqual(sent["data"]["id"], "tx-1")
        self.assertTrue(
            any(path.endswith("/transactions") for path, _ in vault.fake_client.posts)
        )

    def test_preview_and_order_wrappers(self):
        rail = AdvancedTradeSpot(FakeVault())
        preview = rail.preview_market_buy("eth-usdc", Decimal("5"))
        self.assertEqual(preview["product_id"], "ETH-USDC")
        buy = rail.market_buy("btc-usd", Decimal("3"), "test-buy")
        self.assertTrue(buy["success"])
        sell = rail.market_sell("btc-usd", Decimal("0.001"), "test-sell")
        self.assertTrue(sell["success"])


    def test_paginated_coinbase_accounts_include_second_page_and_sum_currency(self):
        class PagedClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.account_calls = []
            def get_accounts(self, **kwargs):
                self.account_calls.append(kwargs)
                if kwargs.get("cursor") == "more":
                    return FakeResponse({"accounts": [
                        {"currency": "USD",
                         "available_balance": {"value": "40"}, "hold": {"value": "0"}},
                        {"currency": "USDC",
                         "available_balance": {"value": "4"}, "hold": {"value": "1"}},
                    ], "has_next": False})
                return FakeResponse({"accounts": [
                    {"currency": "USDC",
                     "available_balance": {"value": "6"}, "hold": {"value": "2"}},
                ], "has_next": True, "cursor": "more"})
        vault = FakeVault()
        vault.fake_client = PagedClient()
        status = AdvancedTradeSpot(vault).status()
        values = {balance.currency: (balance.available, balance.hold)
                  for balance in status.balances}
        self.assertEqual(values["USD"], (Decimal("40"), Decimal("0")))
        self.assertEqual(values["USDC"], (Decimal("10"), Decimal("3")))
        self.assertEqual(len(vault.fake_client.account_calls), 2)

    def test_accounts_missing_next_cursor_block_partial_wallet_view(self):
        vault = FakeVault()
        vault.fake_client.get_accounts = lambda **kwargs: FakeResponse({
            "accounts": [], "has_next": True, "cursor": ""})
        with self.assertRaisesRegex(RuntimeError, "cursor missing"):
            AdvancedTradeSpot(vault).status()

    def test_accounts_repeated_cursor_is_not_complete(self):
        vault = FakeVault()
        vault.fake_client.get_accounts = lambda **kwargs: FakeResponse({
            "accounts": [], "has_next": True, "cursor": "same"})
        with self.assertRaisesRegex(RuntimeError, "cursor repeated"):
            AdvancedTradeSpot(vault).status()

    def test_fill_history_missing_required_cursor_is_not_complete(self):
        vault = FakeVault()
        vault.fake_client.get_fills = lambda **kwargs: FakeResponse({
            "fills": [], "has_next": True, "cursor": ""})
        rail = AdvancedTradeSpot(vault)
        with self.assertRaisesRegex(RuntimeError, "cursor missing"):
            rail.account_fills()
        with self.assertRaisesRegex(RuntimeError, "cursor missing"):
            rail.position_fills("BTC-USD")

    def test_explicit_final_fill_page_does_not_request_spurious_cursor(self):
        vault = FakeVault()
        calls = []
        def final_page(**kwargs):
            calls.append(kwargs)
            return FakeResponse({
                "fills": [], "has_next": False, "cursor": "stale-next"})
        vault.fake_client.get_fills = final_page
        rail = AdvancedTradeSpot(vault)
        self.assertTrue(rail.account_fills()["complete"])
        self.assertTrue(rail.position_fills("BTC-USD")["complete"])
        self.assertEqual(len(calls), 2)

    def test_invalid_account_balance_does_not_mark_account_valid(self):
        vault = FakeVault()
        vault.fake_client.get_accounts = lambda **kwargs: FakeResponse({
            "accounts": [{"currency": "USD",
                          "available_balance": {"value": "NaN"},
                          "hold": {"value": "0"}}], "has_next": False})
        with self.assertRaisesRegex(RuntimeError, "nonfinite"):
            AdvancedTradeSpot(vault).status()


    def test_order_reads_reject_credential_switch_before_client_construction(self):
        # A scope check just before an HTTP call is not enough. The vault
        # must bind the exact credentials used to construct that HTTP client.
        backend = MemoryKeyring()
        with patch.dict(os.environ, {}, clear=True):
            vault = AdvancedTradeVault(backend=backend)
            vault.save("key-a", "secret-a")
            rail = AdvancedTradeSpot(vault)
            original_scope = rail.account_scope_id()
            vault.save("key-b", "secret-b")
            with patch("src.wallets.advanced_trade.RESTClient") as client:
                with self.assertRaisesRegex(RuntimeError, "credentials changed"):
                    rail.order_detail("order-a", expected_scope=original_scope)
                with self.assertRaisesRegex(RuntimeError, "credentials changed"):
                    rail.find_order_id_by_client_id(
                        product_id="BTC-USD", client_order_id="client-a",
                        expected_scope=original_scope)
                with self.assertRaisesRegex(RuntimeError, "credentials changed"):
                    rail.order_fills("order-a", expected_scope=original_scope)
                client.assert_not_called()

    def test_order_client_binds_exact_authenticated_credential_scope(self):
        # Regression: a key switch after desktop snapshot verification but
        # before the HTTP client is constructed must not submit under key B.
        backend = MemoryKeyring()
        with patch.dict(os.environ, {}, clear=True):
            vault = AdvancedTradeVault(backend=backend)
            vault.save("key-a", "secret-a")
            rail = AdvancedTradeSpot(vault)
            snapshot_scope = rail.account_scope_id()
            vault.save("key-b", "secret-b")
            with patch("src.wallets.advanced_trade.RESTClient") as client:
                with self.assertRaisesRegex(RuntimeError, "credentials changed"):
                    rail.market_buy("BTC-USD", Decimal("10"), "client-a",
                                    expected_scope=snapshot_scope)
                with self.assertRaisesRegex(RuntimeError, "credentials changed"):
                    rail.market_sell("BTC-USD", Decimal("0.01"), "client-a",
                                     expected_scope=snapshot_scope)
                client.assert_not_called()
                # Returning to the original credential must still work.
                vault.save("key-a", "secret-a")
                client.return_value = FakeClient()
                rail.market_buy("BTC-USD", Decimal("10"), "client-a",
                                expected_scope=snapshot_scope)
                self.assertEqual(client.call_args.kwargs["api_key"], "key-a")


if __name__ == "__main__":
    unittest.main()
