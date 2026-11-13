"""Actual Python Qt live-mode admission tests. No Coinbase requests or orders."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from src.advanced_live import AdvancedSpotEngine
from src.ui import MainWindow


def pretend_snapshot():
    return {
        "cash": 25.0, "starting_cash": 25.0, "net_liquidation": 25.0,
        "pnl": 0.0, "multiple": 1.0, "positions": {}, "prices": {},
        "wallet_address": "Coinbase Advanced portfolio",
        "pnl_is_verified_trading_profit": False,
        "bean_account": {
            "capital": {"starting_equity_usd": 30, "current_equity_usd": 25},
            "execution": {"status": "COMPLETE", "fill_count_retained": 4},
            "realized_account_profit_verified": False,
        },
    }


class PythonDesktopAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "LOCALAPPDATA": self.temp.name, "APPDATA": self.temp.name})
        self.env.start()
        self.window = MainWindow()
        def fake_sync(engine):
            engine._snapshot = pretend_snapshot()
            return engine._snapshot
        self.sync_patch = patch.object(AdvancedSpotEngine, "sync", fake_sync)
        self.sync_patch.start()
        self.window.execution.blockSignals(True)
        self.window.execution.setCurrentIndex(3)
        self.window.execution.blockSignals(False)
        self.window._switch_to_live("advanced")
        self.assertIsInstance(self.window.engine, AdvancedSpotEngine)

    def tearDown(self):
        self.window.stop_autonomy() if self.window.timer.isActive() else None
        self.window.close()
        self.sync_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_live_start_blocked_before_any_engine_step_if_preflight_fails(self):
        attempted = []
        self.window.engine.step = lambda: attempted.append("TRADE")
        with (patch("src.ui.inspect_advanced", return_value={
                "execution_ready": False, "blockers": ["UNRECONCILED_COINBASE_SUBMISSION"]}) as inspect,
              patch("src.ui.QMessageBox.critical") as message):
            self.window.start_autonomy()
        inspect.assert_called_once()
        message.assert_called_once()
        self.assertEqual(attempted, [])
        self.assertFalse(self.window.timer.isActive())
        self.assertTrue(self.window.start_button.isEnabled())

    def test_no_coinbase_identity_or_failed_read_only_call_prevents_start(self):
        attempted = []
        self.window.engine.step = lambda: attempted.append("TRADE")
        with (patch("src.ui.inspect_advanced", side_effect=TimeoutError("offline")) as inspect,
              patch("src.ui.QMessageBox.critical") as message):
            self.window.start_autonomy()
        inspect.assert_called_once()
        message.assert_called_once()
        self.assertEqual(attempted, [])
        self.assertFalse(self.window.timer.isActive())

    def test_live_start_performs_preflight_before_first_cycle(self):
        sequence = []
        def inspector(**kwargs):
            self.assertEqual(kwargs["db_path"], self.window.store.path)
            self.assertIs(kwargs["rail"], self.window.engine.rail)
            self.assertIs(kwargs["vault"], self.window.engine.rail.vault)
            sequence.append("preflight")
            return {"execution_ready": True, "blockers": []}
        self.window.engine.step = lambda: sequence.append("cycle")
        with patch("src.ui.inspect_advanced", side_effect=inspector):
            self.window.start_autonomy()
        self.assertEqual(sequence, ["preflight", "cycle"])
        self.assertTrue(self.window.timer.isActive())

    def test_authenticated_bean_capital_and_fill_count_shown_in_actual_qt_ui(self):
        self.window.engine._snapshot = pretend_snapshot()
        self.window.refresh()
        evidence = self.window.bean_account.text()
        self.assertIn("starting observed: $30.00", evidence)
        self.assertIn("current: $25.00", evidence)
        self.assertIn("authenticated fills: 4", evidence)
        self.assertIn("NOT RECONCILED", evidence)


if __name__ == "__main__":
    unittest.main()
