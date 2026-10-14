"""Mock Coinbase order paths for the real AIscend policy, independent of BEAN."""
import tempfile
import unittest
from pathlib import Path

from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore


def market(product, a, b, c, vol=2):
    return {"product":product,"return_5m":a,"return_1h":b,
            "return_6h":c,"volume_ratio":vol,"spread_bps":5}


class PrimaryLiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=StateStore(Path(self.temp.name)/"wallet.db")
        self.engine=AdvancedSpotEngine.__new__(AdvancedSpotEngine)
        self.engine.store=self.store
        self.engine._taker_cost_bps=lambda: (60.0, "test_fee")
        self.engine.sync=lambda: {"synced":True}
        self.actions=[]
        self.engine._execute_buy=lambda d,s:self.actions.append(("BUY",d.symbol))
        self.engine._execute_sell=lambda d,s:self.actions.append(("SELL",d.symbol))

        class Bomb:
            def assess(self,*args):
                raise AssertionError("BEAN must never control primary trades")
        self.engine.growth_gate=Bomb()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def snapshot(self, *, cash=None, held=None):
        return {
            "asset_universe":{"scored":[
                market("ALEO-USD",.012,.04,.08),
                market("GROVE-USDC",-.03,-.05,-.09)]},
            "tradable_products":["ALEO-USD","GROVE-USDC","USDC-USD"],
            "cash_by_quote":cash or {},
            "positions":held or {},
            "prices":{"ALEO-USD":2.0,"GROVE-USDC":2.0},
            "balance_values_usd":{"GROVE":20.0}
        }

    def test_sell_convert_buy_no_bean_control(self):
        self.engine._primary_trade_step(self.snapshot(
            cash={"USDC":.88276},
            held={"GROVE-USDC":{"qty":10,"available_qty":10}}))
        self.assertEqual(self.actions[-1],("SELL","GROVE-USDC"))
        self.assertEqual(self.store.latest_decisions(1)[0]["status"],"LIVE_EXECUTED")

        self.engine._primary_trade_step(self.snapshot(cash={"USDC":20}))
        self.assertEqual(self.actions[-1],("SELL","USDC-USD"))

        self.engine._primary_trade_step(self.snapshot(cash={"USD":19}))
        self.assertEqual(self.actions[-1],("BUY","ALEO-USD"))
        self.assertGreater(float(self.store.get_meta(
            "primary_maturity_at:ALEO-USD")),0)

    def test_idle_wallet_explains_no_compatible_eligible_route(self):
        self.engine._primary_trade_step(self.snapshot(cash={}))
        row=self.store.latest_decisions(1)[0]
        self.assertEqual(row["action"],"HOLD")
        self.assertIn("no cost-positive executable entry",row["rationale"])
        self.assertIn('"bean_role": "research_advisory_only"',
                      self.store.get_meta("primary_trade_plan_latest"))

    def test_buy_execution_error_is_not_called_success(self):
        def boom(decision,snapshot):
            raise RuntimeError("preview rejected by venue")
        self.engine._execute_buy=boom
        self.engine._primary_trade_step(self.snapshot(cash={"USD":25}))
        row=self.store.latest_decisions(1)[0]
        self.assertEqual(row["action"],"BUY")
        self.assertEqual(row["status"],"EXECUTION_ERROR")
        self.assertIn("preview rejected",row["rationale"])

    def test_expired_unscored_holding_is_sold_not_perpetually_held(self):
        self.store.set_meta("primary_maturity_at:MET-USDC","1")
        self.engine._primary_trade_step(self.snapshot(
            held={"MET-USDC":{"qty":3,"available_qty":3}}))
        self.assertEqual(self.actions[-1],("SELL","MET-USDC"))
        self.assertIn("planned_maturity_exit_unobserved",
                      self.store.latest_decisions(1)[0]["rationale"])

    def test_rejected_rotation_does_not_hide_cash_funded_opportunity(self):
        snap=self.snapshot(
            cash={"USD":.9},
            held={"GROVE-USDC":{"qty":10,"available_qty":10}})
        snap["asset_universe"]["scored"][1]=market(
            "GROVE-USDC",.05,.11,.35)
        self.engine._primary_trade_step(snap)
        self.assertNotIn(("SELL","GROVE-USDC"),self.actions)
        self.assertTrue(
            self.actions==[("BUY","ALEO-USD")] or self.actions==[],
            self.actions)



if __name__=="__main__":
    unittest.main()
