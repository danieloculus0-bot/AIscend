# BEAN learning push: complete outcome and checkpoint index

This index records every tested method, including losses. It does not select a hindsight winner or establish best-on-market performance. All three April-through-September windows are already inspected historical development periods on eight present-selected Binance USDT instruments. The modeled bankroll is 1,000 quote units split into funded sleeves; USD conversion and executable exchange fills are not verified.

## Main findings

Chunked ingestion with gradient digestion improved on ingest-then-train in all three tested windows. Complete-row sampling is now verified in six additional candidates, but complete coverage did not consistently improve returns. Equivalent-action constraints and exit-cost/reward changes also produced mixed financial results. Accounting and training correctness are necessary conditions, not evidence of a market-prediction edge. No revised model has been independently qualified or activated for live trading.

## Every development outcome

| Method | 2023 Apr-Sep | 2024 Apr-Sep | 2025 Apr-Sep |
| --- | ---: | ---: | ---: |
| buy_hold | -16.98% | -22.43% | +71.65% |
| cash | +0.00% | +0.00% | +0.00% |
| chunked_alias_consistent | -9.13% | -5.44% | +17.19% |
| chunked_net_liquidation_reward | -8.75% | -2.75% | -1.41% |
| economic_overlay | -23.72% | -19.81% | +6.55% |
| factor | -19.95% | -20.15% | +13.72% |
| factor_exit_reserve | -22.77% | -11.48% | -13.93% |
| full_pass_gross | -3.77% | -9.71% | +5.80% |
| full_pass_net_liquidation | -1.38% | -6.20% | +0.36% |
| past_mean_forecast | +0.00% | +0.00% | +0.00% |
| raw_chunked | +6.65% | -3.84% | +0.33% |
| raw_full | -1.73% | -9.28% | +4.68% |
| raw_limited | -9.72% | -7.86% | -7.21% |
| training_selected_expert | -2.66% | -25.52% | +4.00% |

The training-selected expert varies by window. Its fallback profit belongs to that rule, not to the neural model. Constant past-mean forecasts and buy/hold are retained as simple controls. Different methods and repeated source cases are not independent market evidence.

## Retention and verification

The index contains 42 outcome records and 21 current checkpoint references, backed by 8 immutable upstream result/manifest files. All referenced checkpoint receipts report successful full-state recovery, including optimizer, target, RNG and replay. Earlier replicated experiments remain in their original folders and Git history.

There are 47,520 raw action/exposure labels over 1,440 distinct asset/period outcomes; the latter are still correlated across coins and dates. Every source row in each full-pass candidate reached an actual training batch before eviction. This proves data participation, not that each case was understood or profitably generalized.

`learning_outcomes.sqlite3.gz` contains outcome, checkpoint and provenance tables. Each outcome includes its exact upstream JSON pointer, pinned source commit and Git blob hash. No missing individual events were reconstructed from aggregate totals. The original complete decision ledgers and model files remain upstream.

Rebuild with `python research/bean_learning_push_20261024/build_summary.py`. For source verification, pass `--source-root /path/to/checkout` checked out at the pinned source commit. Full source paths and hashes are in `source_summary.json`.

## Next qualification push

Use the corrected training/valuation contracts on broader venue-eligible data, with realistic quote conversion, order minimums, spreads and missing-market handling. Keep new asset/date evaluation separate from the development corpus. Add multi-step trade management so holding-period decisions and future execution costs are represented explicitly. Compare fixed experts, simple predictive baselines and BEAN under identical conditions before claiming learned improvement. The existing full Coinbase collectors and broader archived price history should be reused, not replaced by another small-market sample. Prospective paper performance and independently comparable external benchmarks are required before any market-rank claim.

## Using a retained model

Use BEAN Core revision `887935ae816b4ce6175c6cde54d749f0e5cf3268` and verify the state-file hash before `SpongeDQN.from_training_state`. Respect its knowledge cutoff and the matching source policy/valuation convention. The neural head alone is not the complete trader: factored heads predict market components, and raw-head policies may require alias constraints or an expert reference. This index does not auto-activate a model or update the hosted BEAN notes connector.

