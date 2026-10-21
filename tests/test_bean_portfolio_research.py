"""Cross-repository, credential-free AIscend learning research contract."""
import os
from pathlib import Path
import tempfile
import unittest

from src.bean_core_bridge import record_strategy_research_outcome, strategy_research_context


class AIscendBeanPortfolioResearch(unittest.TestCase):
    def test_research_reward_context_is_advisory_only(self):
        core = os.environ.get("BEAN_CORE_DIR")
        if not core:
            self.skipTest("Real experimental BEAN Core checkout required")
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"learning.sqlite"
            args=dict(strategy="holdout",event_id="eval_001",
                      state="cash/flat",action="investigate",reward=1.25,
                      next_state="cash/investigated",
                      evaluation_ref="test://matched-001",kind="synthetic")
            first=record_strategy_research_outcome(core,path,**args)
            self.assertTrue(first["result"]["new"])
            self.assertFalse(first["trade_authorized"])
            self.assertFalse(record_strategy_research_outcome(core,path,**args)["result"]["new"])
            context=strategy_research_context(core,path,strategy="holdout",query="cash")
            self.assertFalse(context["trade_authorized"])
            self.assertEqual(context["result"]["total_events"],1)
            self.assertGreater(len(context["result"]["policy"]),0)
            with self.assertRaises(ValueError):
                record_strategy_research_outcome(core,path,**{**args,"kind":"unverified","event_id":"eval_002"})


if __name__ == "__main__":
    unittest.main()
