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

    def test_invalid_cost_rejected(self):
        with self.assertRaisesRegex(ValueError,"INVALID_COST"):
            walkforward(synthetic(2400),fee_bps=float("nan"))


if __name__=="__main__":
    unittest.main()
