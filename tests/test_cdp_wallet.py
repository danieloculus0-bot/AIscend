import os
import unittest
from unittest.mock import patch

from src.wallets.cdp_wallet import CredentialVault, SERVICE_NAME


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, key):
        return self.values.get((service, key))

    def set_password(self, service, key, value):
        self.values[(service, key)] = value

    def delete_password(self, service, key):
        self.values.pop((service, key), None)


class BrokenKeyring:
    def get_password(self, service, key):
        raise RuntimeError("no backend")


class CredentialVaultTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeKeyring()
        self.vault = CredentialVault(backend=self.backend)

    def test_not_configured_when_empty(self):
        self.assertFalse(self.vault.configured())

    def test_save_marks_vault_configured(self):
        self.vault.save("id", "secret", "wallet")
        self.assertTrue(self.vault.configured())
        self.assertEqual(
            self.backend.get_password(SERVICE_NAME, "CDP_API_KEY_ID"),
            "id",
        )

    def test_load_into_environment(self):
        self.vault.save("id", "secret", "wallet")
        with patch.dict(os.environ, {}, clear=True):
            self.vault.load_into_environment()
            self.assertEqual(os.environ["CDP_API_KEY_ID"], "id")
            self.assertEqual(os.environ["CDP_API_KEY_SECRET"], "secret")
            self.assertEqual(os.environ["CDP_WALLET_SECRET"], "wallet")

    def test_clear_removes_credentials(self):
        self.vault.save("id", "secret", "wallet")
        self.vault.clear()
        self.assertFalse(self.vault.configured())

    def test_blank_secret_rejected(self):
        with self.assertRaises(ValueError):
            self.vault.save("id", "", "wallet")

    def test_missing_keyring_backend_is_not_configured(self):
        vault = CredentialVault(backend=BrokenKeyring())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(vault.configured())

    def test_environment_credentials_work_without_keyring_backend(self):
        vault = CredentialVault(backend=BrokenKeyring())
        values = {
            "CDP_API_KEY_ID": "id",
            "CDP_API_KEY_SECRET": "secret",
            "CDP_WALLET_SECRET": "wallet",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertTrue(vault.configured())
            vault.load_into_environment()


if __name__ == "__main__":
    unittest.main()
