"""BEAN research tests. All candles synthetic, all orders hypothetical."""
import tempfile
import unittest
from pathlib import Path

from src import bean_profit_lab as lab


def candles(n=480, change=.0001):
    data = {}
    price = 100.
    for i in range(n * lab.AGGREGATE_BARS):
        opening = price
        price *= 1 + change
        data[i * lab.BAR] = {
            "open": opening, "high": max(opening, price),
            "low": min(opening, price), "close": price, "volume": 1.0,
        }
    return data


class BeanProfitLedgerTests(unittest.TestCase):
    def test_us_entry_taker_baseline_is_currently_modeled(self):
        self.assertEqual(lab.DEFAULT_TAKER_FEE_BPS, 90.0)
        self.assertEqual(lab.COST_MODEL, "coinbase_next_4h_open_taker90_spread10_v2")

    def test_completed_bars_only(self):
        rows = lab.to_4h(candles())
        self.assertEqual(len(rows), 480)
        first_signal = lab.signal(rows[:90], "trend_3d")
        first_context = lab.bean_context(rows[:90])
        rows[90]["close"] *= 10
        self.assertEqual(first_signal, lab.signal(rows[:90], "trend_3d"))
        self.assertEqual(first_context, lab.bean_context(rows[:90]))

    def test_next_open_fees_not_forgiven(self):
        rows = lab.to_4h(candles(n=220, change=0))
        result = lab.simulate(rows, "buy_hold", 43, 100)
        self.assertAlmostEqual(result["net_return"],
                               (1 - .0095)**2 - 1, places=7)
        self.assertEqual(result["orders"], 2)

    def test_flat_market_drawdown_includes_final_liquidation_fee(self):
        rows = lab.to_4h(candles(n=220, change=0))
        result = lab.simulate(rows, "buy_hold", 43, 100)
        expected = 1 - (1 - (90 + 10 / 2) / 10000) ** 2
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["orders"], 2)
        self.assertAlmostEqual(result["drawdown"], expected, places=7)
        self.assertAlmostEqual(result["drawdown"], -result["net_return"], places=7)

    def test_gap_in_owned_position_fails_instead_of_skipping_loss(self):
        rows = lab.to_4h(candles(n=220))
        del rows[100]
        result = lab.simulate(rows, "buy_hold", 43, 180)
        self.assertEqual(result["status"], "DATA_GAP")

    def test_coinbase_candle_outage_cannot_form_fictitious_four_hour_bar(self):
        raw = candles(n=185)
        del raw[100 * lab.AGGREGATE_BARS * lab.BAR]
        rows = lab.to_4h(raw)
        self.assertEqual(len(rows), 184)
        self.assertNotIn(100 * lab.AGGREGATE_BARS * lab.BAR,
                         [r["ts"] for r in rows])

    def test_ledger_detects_tampering(self):
        journal = lab.Ledger()
        journal.append("decision", value=1)
        self.assertEqual(lab.chain(journal.entries), journal.tip)
        journal.entries[0]["value"] = 2
        with self.assertRaises(ValueError):
            lab.chain(journal.entries)

    def test_no_ledger_overwrite(self):
        journal = lab.Ledger()
        journal.append("decision", value=1)
        with tempfile.TemporaryDirectory() as folder:
            location = Path(folder)/"record.jsonl"
            journal.save(location)
            with self.assertRaises(FileExistsError):
                journal.save(location)

    def test_no_promotion_from_historical_holdout(self):
        result = lab.experiment("BTC-USD", candles(n=220))
        self.assertFalse(result["promotion_authorized"])
        self.assertEqual(lab.chain(result["ledger_events"]),
                         result["ledger_tip"])
        kind = [x["type"] for x in result["ledger_events"]
                if x["type"] not in ("causal_decision", "bean_q_reward_update")]
        self.assertEqual(kind, ["preregister", "bean_train_expert",
                                "freeze_before_holdout", "historical_holdout_result"])

    def test_flat_market_has_no_fee_net_edge(self):
        result = lab.experiment("BTC-USD", candles(n=220, change=0))
        self.assertEqual(result["status"], "NOT_PROVEN")

    def test_bean_policy_updates_q_only_after_bar(self):
        rows = lab.to_4h(candles(n=210, change=.001))
        journal = lab.Ledger()
        run = lab.simulate(rows, "bean_q", 43, 100, ledger=journal,
                           phase="training", product="BTC-USD")
        self.assertEqual(run["status"], "OK")
        self.assertTrue(run["learner_q"])
        first_reward = next(r for r in journal.entries
                            if r["type"] == "bean_q_reward_update")
        self.assertEqual(first_reward["observed_after_ts"],
                         rows[43]["ts"] + lab.BAR*lab.AGGREGATE_BARS)
        self.assertEqual(lab.chain(journal.entries), journal.tip)

    def test_validation_does_not_override_champion_after_holdout(self):
        result = lab.experiment("ETH-USD", candles(n=300))
        freeze = next(e for e in result["ledger_events"]
                      if e["type"] == "freeze_before_holdout")
        final = result["ledger_events"][-1]
        self.assertEqual(freeze["policy"], result["champion"])
        self.assertEqual(final["policy"], freeze["policy"])


if __name__ == "__main__":
    unittest.main()
