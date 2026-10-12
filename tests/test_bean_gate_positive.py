import unittest
from scripts.bean_strategy_gate import judge

def receipt(n):
    t=n*10000000
    return dict(historical_source="Coinbase public candles",
        strategy={"rule":"momentum"},dataset_sha256=format(n,"064x"),
        pairs=["BTC-USD"],cost_per_side=.006,
        fill_policy="previous_close_signal_next_open_fill",
        starting_bankroll=25,
        training_window=dict(start_ts=t,end_ts=t+360000),
        holdout_window=dict(start_ts=t+360000,end_ts=t+720000),
        holdout=dict(final=30,net_return=.2,max_drawdown=.1,trades=8),
        buy_hold_btc=dict(final=27),cash=dict(final=25),
        beats_btc_holdout=True,beats_cash_holdout=True)

class DisjointGate(unittest.TestCase):
    def test_positive_path_and_duplicate_rejection(self):
        self.assertTrue(judge([receipt(1),receipt(2)])["accepted"])
        self.assertFalse(judge([receipt(1),receipt(1)])["accepted"])
