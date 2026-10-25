# BEAN causal portfolio and retained-learning result

**Economic result: FAILED.** The retained policy did not learn a method that worked on the final historical period. No model from this experiment qualifies as a profitable method.

The policy saw trailing observations, its starting/current equity, cash, positions, costs and matured trade returns. The environment supplied a simulated execution at the next reference observation and revealed the outcome only afterward. The policy process was never given a future-price matrix or dataset filename. Source and protocol hashes identify the exact code/data. This is a reference-price research simulation, not executable exchange-fill evidence.

## Final historical period

2025-10-24 through 2026-10-23; each arm began with $1,000. Modeled per-side costs: 90 bps fee, 5 bps half-spread, 5 bps slippage. Policies chose every two days. Current-vintage historical prices and volume were used, with no news backdating.

| Arm | Ending equity | Net return | Maximum drawdown | Modeled costs | Orders |
| --- | ---: | ---: | ---: | ---: | ---: |
| selected_frozen | $47.99 | -95.20% | 96.04% | $579.99 | 819 |
| selected_online_matured_feedback | $55.38 | -94.46% | 94.46% | $531.89 | 1016 |
| same_architecture_untrained | $1,000.00 | 0.00% | 0.00% | $0.00 | 0 |
| past_reward_placebo | $78.98 | -92.10% | 92.61% | $471.10 | 818 |
| cash | $1,000.00 | 0.00% | 0.00% | $0.00 | 0 |
| buy_hold_btc | $717.39 | -28.26% | 48.91% | $17.15 | 2 |
| relative_strength_rotation | $6.00 | -99.40% | 99.60% | $287.04 | 849 |

The frozen learned policy incurred $579.99 in modeled costs and $372.02 in gross trading losses on the quantities it actually traded. Adding costs back is an accounting decomposition, not a fee-free rerun with different position sizes. The online arm also lost money despite receiving 182 further matured-outcome updates. There were zero missing held-asset marks in these final-period arms, so the failure cannot be attributed to the missing-price write-down rule.

## Development and selection

| Candidate | Engine | Training updates | Validation return | Restart recovered |
| --- | --- | ---: | ---: | --- |
| td-7 | td | 447 | 23.26% | True |
| dqn-7 | dqn | 1536 | -87.82% | True |
| dqn-41 | dqn | 1536 | -92.23% | True |
| dqn-20261024 | dqn | 1536 | -93.22% | True |

The selection rule was locked before the final period was evaluated. It chose `td-7` based on its +23.26% validation return. All three actual neural DQN candidates updated their weights, but all lost money in validation. The neural implementation was included and tested; it was not declared superior because it was more complex. The final-period results were not used to reselect a different model or tune the algorithm.

## Retention and scope

- Four candidate policies changed their actual Q values or neural weights and recovered the same learned values after restart.
- The frozen policy retained 447 TD updates. The continuing policy ended with 629 and was subsequently reopened and verified separately.
- Neural checkpoints preserve online and target weights, Adam state, replay, RNG and counters; tabular checkpoints preserve Q values and update counters.
- Six learned policy artifacts are indexed in `retention_manifest.json`, with file/state hashes and knowledge cutoffs. Reusing them before those cutoffs is rejected by the recovery tool.
- The complete experiment contains 4,237 SHA-linked decision records in both compressed JSONL and SQLite forms. Negative outcomes remain included.
- The fresh source collection contains 1,192,530 price observations across 1,141 assets, plus volume observations. The combined candidate universe has 1,914 asset series, including short histories and some non-crypto instruments. Eligibility uses past history/liquidity only. During the final period, 589-656 assets were eligible at a decision; the frozen and online policies traded 121 and 151 distinct assets respectively.
- The 20 actions are implemented price/volume variants from the catalog, including cash and holding. Options, orderbook and DeFi engines are not simulated by daily reference prices.
- Earlier aborted development attempts and their exact failure artifacts are preserved separately. They did not open a final-period selection lock and are not counted as valid economic results.

## Reuse retained state

Install `requirements-research.txt`, check out BEAN revision `887935ae816b4ce6175c6cde54d749f0e5cf3268`, then run from AIscend:

```bash
python -m scripts.bean_retained_policy --directory research/bean_causal_retention_20261024_verified --core-dir ../BEAN --policy-id selected_online_matured_feedback --as-of 2026-10-25T00:00:00Z
```

This verifies recovery and a diagnostic decision. It does not place orders or turn an unprofitable policy into a successful strategy. All these policies remain available as failed research evidence. A revised method needs a new evaluation protocol and fresh unseen data; this final year has now been inspected.

Coin Metrics data carries CC BY-NC 4.0. Commercial data rights are not established.
