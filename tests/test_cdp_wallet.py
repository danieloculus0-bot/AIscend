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


if __name__ == "__main__":
    unittest.main()
