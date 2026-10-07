import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.core import Decision, StateStore
from src.live import LiveEngine
from src.wallets.cdp_wallet import SwapExecution, TradingSnapshot


class BuyOnce:
    def decide(self, snapshot):
        return Decision("BUY", "WETH", 0.5, "live test buy")


class FakeWallet:
    def __init__(self):
        self.usdc = Decimal("10")
        self.weth = Decimal("0")
        self.price = Decimal("2500")

    async def trading_snapshot(self):
        return TradingSnapshot(
            address="0x123",
            network="base",
            usdc=self.usdc,
            weth=self.weth,
            weth_price_usdc=self.price,
            net_usdc=self.usdc + self.weth * self.price,
        )

    async def swap_usdc_to_weth(self, fraction):
        spent = self.usdc * Decimal(str(fraction))
        bought = spent / self.price
        self.usdc -= spent
        self.weth += bought
        return SwapExecution(
            user_op_hash="0xabc",
            from_symbol="USDC",
            from_amount=spent,
            to_symbol="WETH",
            to_amount=bought,
        )

    async def swap_weth_to_usdc(self, fraction):
        sold = self.weth * Decimal(str(fraction))
        received = sold * self.price
        self.weth -= sold
        self.usdc += received
        return SwapExecution(
            user_op_hash="0xdef",
            from_symbol="WETH",
            from_amount=sold,
            to_symbol="USDC",
            to_amount=received,
        )


class LiveEngineTests(unittest.TestCase):
    def make_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = StateStore(Path(temp.name) / "live.db")
        self.addCleanup(store.close)
        return store

    def test_live_buy_executes_against_wallet_and_updates_snapshot(self):
        store = self.make_store()
        wallet = FakeWallet()
        engine = LiveEngine(store, wallet, BuyOnce())

        after = engine.step()

        self.assertAlmostEqual(after["cash"], 5.0, places=6)
        self.assertAlmostEqual(after["positions"]["WETH"]["qty"], 0.002, places=9)
        self.assertAlmostEqual(after["net_liquidation"], 10.0, places=6)
        decision = store.latest_decisions(1)[0]
        self.assertEqual(decision["status"], "LIVE_EXECUTED")
        ledger = store.latest_ledger(1)[0]
        self.assertEqual(ledger["kind"], "LIVE_BUY")


if __name__ == "__main__":
    unittest.main()
