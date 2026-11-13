"""Offline testing for actual causal learning and negative research findings."""
import copy
import unittest
from scripts.bean_memory_hypotheses import replay_market, _pick


A = "('donchian_20_10',)"
B = "('donchian_55_20',)"


def report_for(ret_a=(.10, -.10, .20), ret_b=(-.05, .20, -.10)):
    folds = []
    for i in range(3):
        folds.append({
            "window":i, "test_start":i*360+1200,
            "test_end":i*360+1559,
            "winner":["donchian_20_10"],
            "baseline_training_scores": {A:.10, B:.05},
            "all_rule_test_results_after_close":{
                A:{"net_return":ret_a[i], "max_drawdown":.04},
                B:{"net_return":ret_b[i], "max_drawdown":.04}}})
    net = (1+ret_a[0])*(1+ret_a[1])*(1+ret_a[2])-1
    return {"status":"HISTORICAL_WALKFORWARD_RESEARCH",
            "folds":folds,"net_return":round(net,8),
            "no_memory_net_return":round(net,8)}


class CausalLearningHypothesisTests(unittest.TestCase):
    def test_hypothetical_fee_net_arms_reproduce_original_controls(self):
        data = replay_market(report_for())
        self.assertAlmostEqual(data["final_returns"]["no_memory"], .188, 7)
        self.assertAlmostEqual(data["final_returns"]["bean_existing"], .188, 7)
        self.assertEqual(len(data["fold_results"]),3)
        self.assertEqual(data["fold_results"][2]["strategies"]
                         ["same_rule_confirmation"]["rule"],None)
        self.assertEqual(data["changed_choice_folds"]["no_memory"],0)

    def test_future_outcome_never_changes_past_decision(self):
        original = report_for()
        shocked = report_for(ret_a=(.10,-.10,-.80),ret_b=(-.05,.20,1.0))
        before = replay_market(original)["fold_results"]
        after = replay_market(shocked)["fold_results"]
        self.assertEqual(before[0],after[0])
        self.assertEqual(before[1],after[1])
        # Last fold's actions were locked before its outcome was known.
        self.assertEqual(
            {p:d["rule"] for p,d in before[2]["strategies"].items()},
            {p:d["rule"] for p,d in after[2]["strategies"].items()})

    def test_negative_training_scores_hold_cash(self):
        self.assertIsNone(_pick({A:-.01,B:0.}))
        self.assertEqual(_pick({A:.01,B:.01}),A)

    def test_wrong_strategy_manifest_rejected_not_filled(self):
        data = report_for()
        del data["folds"][0]["all_rule_test_results_after_close"][B]
        with self.assertRaisesRegex(ValueError,"STRATEGY_SET_MISMATCH"):
            replay_market(data)

    def test_replay_inconsistent_with_original_ledger_fails_closed(self):
        data = report_for()
        data["net_return"] += .01
        with self.assertRaisesRegex(ValueError,"CONTROL_REPLAY_MISMATCH"):
            replay_market(data)


if __name__ == "__main__":
    unittest.main()
