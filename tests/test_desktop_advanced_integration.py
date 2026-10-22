"""Headless integration tests for the actual Windows UI/Advanced engine handshake.

Creates a Qt window; no Coinbase account or network connection is used.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.advanced_live import AdvancedSpotEngine
from src.ui import MainWindow


def advanced_snapshot():
    return {
        "cash": 0.8, "starting_cash": 25.0, "net_liquidation": 18.0,
        "pnl": -7.0, "multiple": 0.72,
        "positions": {"GROVE-USDC": {
            "qty": 10.0, "available_qty": 10.0,
            "avg_cost": None, "cost_basis_status": "unverified_without_fills",
            "mark_price": 1.72}},
        "prices": {"GROVE-USDC": 1.72},
        "wallet_address": "Coinbase Advanced portfolio",
    }


class ActualDesktopIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name,
                                         "APPDATA": self.temp.name})
        self.env.start()
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.env.stop()
        self.temp.cleanup()

    def test_advanced_snapshots_provide_live_desktop_contract(self):
        engine = AdvancedSpotEngine.__new__(AdvancedSpotEngine)
        engine._snapshot = advanced_snapshot()
        self.assertIs(engine.snapshot(), engine._snapshot)
        self.window.engine = engine
        self.window.refresh()
        self.assertEqual(self.window.positions.item(0, 0).text(), "GROVE-USDC")
        self.assertEqual(self.window.positions.item(0, 2).text(), "Unverified")
        self.assertEqual(self.window.net_card.value.text(), "$18.0000")

    def test_switching_to_advanced_keeps_window_operable(self):
        def fake_sync(engine):
            engine._snapshot = advanced_snapshot()
            return engine._snapshot
        with patch.object(AdvancedSpotEngine, "sync", fake_sync):
            self.window.execution.blockSignals(True)
            self.window.execution.setCurrentIndex(3)
            self.window.execution.blockSignals(False)
            self.window._switch_to_live("advanced")
            self.assertIsInstance(self.window.engine, AdvancedSpotEngine)
            self.assertEqual(self.window.positions.item(0, 2).text(),
                             "Unverified")
            self.assertFalse(self.window.mode.isEnabled())
            self.window.refresh()
            self.assertEqual(self.window.positions.rowCount(), 1)

    def test_advanced_does_not_offer_inert_llm_controls(self):
        self.window.execution.blockSignals(True)
        self.window.execution.setCurrentIndex(3)
        self.window.execution.blockSignals(False)
        self.window.mode.setCurrentIndex(1)
        self.window.toggle_ai_fields()
        self.assertFalse(self.window.endpoint.isEnabled())
        self.assertFalse(self.window.model.isEnabled())


if __name__ == "__main__":
    unittest.main()
