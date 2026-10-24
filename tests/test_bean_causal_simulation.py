import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import numpy as np
from src.bean_causal_simulation import ACTIONS,Broker,DecisionLedger,Panel,Signals,Worker,VECTOR_SIZE,select_candidate

def panel():
    rng=np.random.default_rng(41)
    p=100*np.exp(np.cumsum(rng.normal(.001,.025,(450,8)),axis=0))
    v=np.full_like(p,5_000_000.)
    return Panel(("btc","eth","a","b","c","d","e","f"),np.arange(450)*86400,p,v,"a"*64)

class CausalSimulationTests(unittest.TestCase):
    def test_future_price_and_volume_changes_cannot_change_current_observation(self):
        original=panel();mutated=copy.deepcopy(original)
        cutoff=260
        mutated.prices[cutoff+1:]=mutated.prices[cutoff+1:]*1000
        mutated.volumes[cutoff+1:]=0
        a,b=Signals(original),Signals(mutated)
        account_a,account_b=Broker(original),Broker(mutated)
        self.assertEqual(a.observation(cutoff,account_a),b.observation(cutoff,account_b))
        self.assertEqual(a.proposals(cutoff,{}),b.proposals(cutoff,{}))
        self.assertEqual(len(a.observation(cutoff,account_a)["vector"]),VECTOR_SIZE)
        self.assertEqual(set(a.proposals(cutoff,{})),set(ACTIONS))

    def test_flat_price_trading_loses_fees_and_cannot_create_cash(self):
        p=panel();p.prices[:]=100
        b=Broker(p)
        b.trade({"btc":1},100,101,np.full(8,1_000_000.))
        self.assertGreaterEqual(b.cash,-1e-7)
        b.liquidate(102)
        self.assertAlmostEqual(b.cash,1000*.99/1.01,places=6)
        self.assertGreater(b.costs,0);self.assertEqual(b.positions,{})

    def test_missing_held_asset_is_not_dropped_from_loss_accounting(self):
        p=panel();p.prices[:]=100
        b=Broker(p);b.trade({"btc":1},100,101,np.full(8,1_000_000.))
        p.prices[102,0]=np.nan
        self.assertLess(b.mark(102),1)
        self.assertGreater(b.gap_marks,0)
        b.liquidate(102);self.assertLess(b.cash,1)

    def test_selection_cannot_read_final_period_scores(self):
        rows=[{"id":"a","validation":{"net_return":.1},"holdout":-100},{"id":"b","validation":{"net_return":.05},"holdout":100}]
        self.assertEqual(select_candidate(rows)["id"],"a")
        rows[0]["holdout"],rows[1]["holdout"]=1e12,-1e12
        self.assertEqual(select_candidate(rows)["id"],"a")

    def test_decision_receipts_survive_restart_and_detect_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"decisions.sqlite3"
            ledger=DecisionLedger(path);ledger.record({"action":"cash","outcome":-.01});first=ledger.tip;ledger.close()
            ledger=DecisionLedger(path);self.assertEqual(ledger.tip,first);self.assertEqual(ledger.seq,1)
            ledger.record({"action":"trend","outcome":.02});ledger.close()
            with sqlite3.connect(path) as c:c.execute("UPDATE decisions SET payload_json='{}' WHERE seq=0")
            with self.assertRaises(ValueError):DecisionLedger(path)

    @unittest.skipUnless(os.environ.get("BEAN_CORE_DIR"),"Explicit real BEAN checkout required")
    def test_actual_core_does_not_accept_unmatured_reward(self):
        core=Path(os.environ["BEAN_CORE_DIR"])
        with tempfile.TemporaryDirectory() as d:
            worker=Worker(core,Path(d)/"policy.sqlite3","dqn",7)
            try:
                initial=worker.call("status")
                with self.assertRaisesRegex(RuntimeError,"not matured"):
                    worker.call("observe",vector=[0.]*VECTOR_SIZE,action=0,reward=10.,next_vector=[0.]*VECTOR_SIZE,done=False,decision_at=100,matured_at=100)
                self.assertEqual(worker.call("status")["updates"],initial["updates"])
            finally:worker.close()

if __name__=="__main__":unittest.main()
