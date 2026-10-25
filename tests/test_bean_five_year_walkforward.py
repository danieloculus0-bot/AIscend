"""Offline regression suite for five-year historical BEAN strategy walk-forward."""
import copy
import math
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from datetime import datetime

from scripts.bean_five_year_walkforward import (
    BAR, TRAIN_BARS, TEST_BARS, FIRST_TEST, contiguous_runs, fetch_five_year,
    walkforward, evaluate_product_history,
)
from src.bean_profit_lab import chain


def synthetic(n=2250, *, shape="bull"):
    data={}
    for i in range(n):
        if shape=="waves":
            # Mixed uptrends, bear legs and inactive ranges.
            price=110+22*math.sin(i/78)+0.06*i + 11*math.sin(i/15)
        else:
            price=110+i*.14
        price=max(10.,price)
        data[i*BAR]={"ts":i*BAR,"open":price,"high":price+1.,
                     "low":price-1.,"close":price,"volume":200.}
    return data


class FiveYearWalkforwardTests(unittest.TestCase):
    def test_training_frozen_before_each_test_window(self):
        data=synthetic(2350,shape="waves")
        report=walkforward(data,product="TEST-USD")
        self.assertEqual(report["status"],"HISTORICAL_WALKFORWARD_RESEARCH")
        self.assertGreaterEqual(report["fold_count"],1)
        self.assertFalse(report["promotion_authorized"])
        self.assertEqual(chain(report["ledger"]),report["ledger_tip"])
        locks=[x for x in report["ledger"]
               if x["type"]=="locked_before_test_window"]
        observations=[x for x in report["ledger"]
                      if x["type"]=="after_test_window_closed"]
        updates=[x for x in report["ledger"]
                 if x["type"]=="bean_feedback_postclose"]
        self.assertEqual(len(locks),report["fold_count"])
        self.assertEqual(len(observations),report["fold_count"])
        self.assertEqual(len(updates),report["fold_count"])
        for locked,obs,update in zip(locks,observations,updates):
            self.assertLess(locked["trained_through"],locked["test_start"])
            self.assertLess(locked["test_start"],obs["observed_through"])
            self.assertEqual(obs["observed_through"],update["observed_through"])

    def test_future_shock_cannot_change_prior_selected_rule(self):
        raw=synthetic(2500,shape="waves")
        initial=walkforward(raw)
        shock=copy.deepcopy(raw)
        first_end=FIRST_TEST+TEST_BARS
        for i in range(first_end,2500):
            price=2000-0.1*(i-first_end)
            shock[i*BAR].update(open=price,high=price+1,low=price-1,close=price)
        later=walkforward(shock)
        self.assertEqual(initial["folds"][0]["winner"],
                         later["folds"][0]["winner"])
        self.assertEqual(initial["folds"][0]["training_scores"],
                         later["folds"][0]["training_scores"])
        self.assertEqual(initial["folds"][0]["fee_net_return"],
                         later["folds"][0]["fee_net_return"])

    def test_candle_gaps_remain_partial_and_not_filled(self):
        raw=synthetic(2300)
        raw.pop(800*BAR)
        parts=contiguous_runs(raw)
        self.assertEqual(len(parts),2)
        fetched={"product":"TEST-USD","data":raw,"expected_bars":2300,
                 "received_bars":2299}
        report=evaluate_product_history(fetched)
        self.assertEqual(report["history_status"],"PARTIAL")
        self.assertFalse(report["promotion_authorized"])

    def test_research_does_not_cherry_pick_longest_past_run(self):
        # Historical gap follows a huge old run; only 180 current bars remain.
        raw=synthetic(2600,shape="waves")
        raw.pop(2400*BAR)
        fetched={"product":"TEST-USD", "data":raw, "expected_bars":2600,
                 "received_bars":2599, "end":2600*BAR}
        result=evaluate_product_history(fetched)
        self.assertEqual(result["history_status"],"PARTIAL")
        self.assertEqual(result["status"],"INSUFFICIENT_HISTORY")
        self.assertEqual(result["longest_run_bars"],2400)
        self.assertEqual(result["selected_recent_run_bars"],199)
        self.assertEqual(result["research_segment_selection"],
                         "latest_contiguous_tail_only_v2")
        self.assertFalse(result["promotion_authorized"])

    def test_older_segment_never_substituted_for_missing_latest_candle(self):
        raw=synthetic(2600,shape="waves")
        raw.pop(2599*BAR)
        fetched={"product":"TEST-USD", "data":raw, "expected_bars":2600,
                 "received_bars":2599, "end":2600*BAR}
        result=evaluate_product_history(fetched)
        self.assertEqual(result["status"],"INSUFFICIENT_HISTORY")
        self.assertEqual(result["reason"],"RECENT_CANDLE_DATA_MISSING")
        self.assertEqual(result["history_status"],"PARTIAL")

    def test_current_recent_contiguous_market_keeps_actual_simulation(self):
        raw=synthetic(2350,shape="waves")
        fetched={"product":"TEST-USD", "data":raw, "expected_bars":2350,
                 "received_bars":2350, "end":2350*BAR}
        result=evaluate_product_history(fetched)
        self.assertEqual(result["status"],"HISTORICAL_WALKFORWARD_RESEARCH")
        self.assertEqual(result["history_status"],"COMPLETE")
        self.assertEqual(result["selected_recent_run_bars"],2350)

    def test_too_few_candles_is_normal(self):
        report=walkforward(synthetic(800))
        self.assertEqual(report["status"],"INSUFFICIENT_HISTORY")

    def test_five_year_public_pagination_without_synthetic_data(self):
        clock=datetime(2026,10,24).timestamp()
        calls=[]
        def fetch(url):
            calls.append(url)
            self.assertIn("granularity=21600",url)
            self.assertIn("/BTC-USD/candles?",url)
            # There is deliberately no actual history in the response.
            return []
        observed=fetch_five_year("BTC-USD",clock=lambda:clock,fetcher=fetch)
        self.assertGreater(observed["expected_bars"],7000)
        self.assertEqual(observed["received_bars"],0)
        self.assertGreater(len(calls),20)
        self.assertEqual(observed["requests"],len(calls))

    def test_winning_trade_is_not_just_positive_candle(self):
        report=walkforward(synthetic(2200,shape="waves"))
        self.assertEqual(report["winning_roundtrips"],
                         sum(f["winning_trades"] for f in report["folds"]))
        self.assertEqual(report["completed_roundtrips"],
                         sum(f["closed_trades"] for f in report["folds"]))
        self.assertGreater(report["simulated_start"],0)

    def test_continuous_buy_and_hold_pays_fees_only_once(self):
        data=synthetic(2600,shape="bull")
        report=walkforward(data)
        first=FIRST_TEST
        last=first+report["fold_count"]*TEST_BARS-1
        expected=(data[last*BAR]["close"]/data[first*BAR]["open"]
                  *(1-(90+10/2)/10000)**2-1)
        self.assertAlmostEqual(report["continuous_buy_hold_return"],
                               expected,places=7)
        self.assertNotAlmostEqual(report["continuous_buy_hold_return"],
                                  report["fold_rebalanced_buy_hold_return"],
                                  places=5)

    def test_learning_on_off_same_candles_same_fees_and_frozen_choices(self):
        report=walkforward(synthetic(2350,shape="waves"), product="TEST-USD")
        self.assertEqual(report["fold_count"],len(report["folds"]))
        self.assertEqual(report["q_weight_predeclared"],
                         [0., .25, 1., 2., 4.])
        learned=baseline=1.
        trial = {str(w):1. for w in report["q_weight_predeclared"]}
        for fold in report["folds"]:
            learned*=1+fold["fee_net_return"]
            baseline*=1+fold["no_memory_fee_net_return"]
            for name in trial:
                trial[name]*=1+fold["q_weight_research"][name]["net_return"]
            self.assertIn("no_memory_winner",fold)
            self.assertIn("baseline_training_scores",fold)
            self.assertEqual(len(fold["all_rule_test_results_after_close"]),7)
            self.assertLess(fold["trained_through"],fold["test_start"])
            self.assertTrue(all(
                math.isfinite(v["net_return"])
                for v in fold["all_rule_test_results_after_close"].values()))
        self.assertAlmostEqual(report["net_return"],learned-1,places=6)
        self.assertAlmostEqual(report["no_memory_net_return"],
                               baseline-1,places=6)
        self.assertAlmostEqual(report["learning_incremental_return"],
                               learned-baseline,places=6)
        self.assertEqual(report["learning_outperformed_no_memory"],
                         learned>baseline)
        for name, value in trial.items():
            self.assertAlmostEqual(report["q_weight_research_only"][name],
                                   value-1,places=6)
        self.assertAlmostEqual(report["q_weight_research_only"]["0.0"],
                               report["no_memory_net_return"],places=7)
        self.assertAlmostEqual(report["q_weight_research_only"]["0.25"],
                               report["net_return"],places=7)
        records=report["ledger"]
        locked=[r for r in records
                if r["type"]=="locked_before_test_window"]
        closed=[r for r in records
                if r["type"]=="after_test_window_closed"]
        self.assertEqual(len(locked),len(closed))
        for a,b in zip(locked,closed):
            self.assertEqual(a["no_memory_winner"],b["no_memory_choice"])
            self.assertEqual(a["test_end"],b["observed_through"])

    def test_future_shock_does_not_change_ablation_choice_for_prior_fold(self):
        source=synthetic(2450,shape="waves")
        first=walkforward(source)
        altered=copy.deepcopy(source)
        for i in range(FIRST_TEST+TEST_BARS,len(altered)):
            candle=altered[i*BAR]
            candle.update(open=50,close=50,high=52,low=48)
        future=walkforward(altered)
        self.assertEqual(first["folds"][0]["no_memory_winner"],
                         future["folds"][0]["no_memory_winner"])
        self.assertEqual(first["folds"][0]["no_memory_fee_net_return"],
                         future["folds"][0]["no_memory_fee_net_return"])
        self.assertEqual(first["folds"][0]["fee_net_return"],
                         future["folds"][0]["fee_net_return"])

    def test_invalid_cost_rejected(self):
        with self.assertRaisesRegex(ValueError,"INVALID_COST"):
            walkforward(synthetic(2400),fee_bps=float("nan"))


if __name__=="__main__":
    unittest.main()
