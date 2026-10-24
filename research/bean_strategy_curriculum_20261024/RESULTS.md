# BEAN strategy curriculum, candle transfer and retained state

The request was to teach strategies, apply their lessons to similar candles and keep learned state. This study does those things using the actual BEAN Core replay learner. The trained policy averaged **+14.54%** in the later development period, but lost in both earlier evaluation folds and underperformed simpler controls. **Development qualification: FAILED.** A robust learned advantage was not established. These historical periods overlap evidence seen in prior research, so none is described as a new unseen profitability test.

## What BEAN learned from

- 14,608 actual OHLCV candles across eight fixed spot symbols, October 2020 through September 2025. All 480 official Binance monthly archives passed their published SHA-256 checksums. Raw archives and receipts are retained in `source/`.
- 47,520 fee-inclusive counterfactual teaching examples, covering all 11 rule actions and cash/half/full starting exposure. There are 14,677 positive, 20,881 negative and 11,962 tied rewards. They reuse correlated market periods and are not that many independent observations.
- Shared, dimensionless 47-value inputs describe trailing returns, trend, volatility, RSI, volume and five actual candle bodies/wicks. No symbol or date identity or future-fitted scaler is an input.
- Fixed strategy rules teach trend, breakout, pullback, reversion, engulfing, hammer, risk scaling, cash and hold. These are explicit example algorithms; candlestick names alone are not evidence of predictive power.
- Neural strategy values are combined with 32 nearest previously matured patterns on other symbols, subject to a fixed support/distance rule. `strategy_lessons.json` provides conditional human-readable evidence. The full signed case memory remains in `teaching_memory.sqlite3.gz`.

The learner is BEAN `SpongeDQN` at `887935ae816b4ce6175c6cde54d749f0e5cf3268`, using replay, Adam and Huber updates with gamma=0 for eight-day strategy-value regression. It is not a new general-purpose model. The policy process receives vectors and mature labels, not historical price files. This experiment uses a standalone research adapter; the hosted BEAN notes connector and live application are not automatically upgraded.

## Repeated training and development results

Two seeds and three update budgets were compared on two chronological forward development folds. All training labels matured before each fold's decision time. The rule selected the configuration with the best worst-fold result, locked before the later development audit. The 2025-10-24 through 2026-10-23 final year from the original experiment was excluded. More gradient steps are not more independent market evidence.

| Seed | Gradient updates | Apr-Sep 2023 | Apr-Sep 2024 |
| --- | ---: | ---: | ---: |
| 7 | 256 | -13.05% | -18.53% |
| 7 | 1024 | -21.22% | -16.43% |
| 7 | 4096 | -25.21% | -23.17% |
| 41 | 256 | -5.92% | -15.94% |
| 41 | 1024 | -11.06% | -19.07% |
| 41 | 4096 | -11.79% | -16.34% |

Selected seed 41, budget 256. Trained through outcomes available by 2025-04-01, then frozen for April-September 2025 development diagnostics. Each symbol has a separately funded $1,000 account. Returns below are equal-weight means across those eight accounts, not a single account or an independently tested cross-asset portfolio.

| Development audit arm | Mean net return | Mean ending account | Worst asset daily-close drawdown |
| --- | ---: | ---: | ---: |
| trained strategy + pattern memory | +14.54% | $1,145.43 | 43.87% |
| cash | +0.00% | $1,000.00 | 0.00% |
| buy_hold | +71.65% | $1,716.46 | 39.64% |
| sma_trend | +5.54% | $1,055.41 | 26.67% |
| donchian_breakout | +19.24% | $1,192.43 | 35.42% |
| pattern_memory_only | +24.59% | $1,245.92 | 36.96% |
| same_architecture_untrained | +31.78% | $1,317.75 | 40.66% |

The untrained architecture retains the same pattern-memory component, isolating neural fitting within the hybrid. The pattern-only control omits the network. All arms use the same dates, symbols, delayed reference-open fills and 1% modeled per-side costs. Drawdown uses daily closes, not intraday executable prices. These examples do not establish current venue fees or tradability.

## Retention and reuse

All twelve intermediate fold checkpoints remain inside four SHA-linked Core journals. The selected final policy retains its weights, target, optimizer, RNG and 4,096 replay cases. Actual selected updates: 256; restart reproduced both learned state and diagnostic values: True. The exact journal file/state hashes and knowledge cutoffs are in `retention_manifest.json`. The final decision evidence contains 3,344 SHA-linked records.

```bash
python -m scripts.bean_strategy_policy --directory research/bean_strategy_curriculum_20261024 --core-dir ../BEAN --as-of 2026-10-25T00:00:00Z
```

An earlier checkpoint is recoverable using `--policy-id fold-0-seed-7-updates-256`; it is recovered by its exact weight hash from the journal rather than replaced by the latest checkpoint. Future learned state is rejected when a requested date predates its cutoff. The original numeric-boundary aborted attempt and its partial losses are preserved in `research/bean_curriculum_aborted_numeric_20261024.zip`.

Continue development using the same explicit teaching/validation procedure, keeping all trials. The current command has bounded budgets so it cannot silently search forever and report only a lucky winner. A revised model needs a separately registered evaluation on genuinely new forward or independent evidence before any claim of useful trading learning. No live orders were sent.

## Limits and provenance

The eight instruments were selected in the present, excluding many failed/delisted instruments. This introduces survivorship, venue and correlated-asset bias. Prices are USDT-denominated archive candles, not USD quotes or orderbook fills. Historical revisions, execution latency, spread/impact, availability and actual fees need venue-specific replication. Repeated known histories cannot prove future profit. No historical first-seen narrative dataset was supplied, and current headlines were not backdated.

Official source documentation: https://github.com/binance/binance-public-data . Source terms: https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md . Original failed portfolio experiment: https://github.com/danieloculus0-bot/AIscend/pull/86 . Core implementation: https://github.com/danieloculus0-bot/BEAN/pull/166 .

READY FOR REVIEW: separate experiment branch; reference-price research only; ten meaningful contract tests passed locally with actual Core gradients and restart. No profitable production behavior is asserted.

## Complete explicit-rule teacher scoreboard

These fixed-rule results cover all available history after 200 warmup bars, within the five-year OHLCV source. They are historical development examples, not learned-policy returns or evidence of future profitability. Every rule, including losses, is included. Each rule starts eight separate $1,000 accounts. These are cumulative equal-weight mean account returns, not annualized.

| Explicit rule | Mean cumulative net return | Worst asset daily-close drawdown |
| --- | ---: | ---: |
| cash | +0.00% | 0.00% |
| hold | +0.00% | 0.00% |
| buy_hold | +83.42% | 96.27% |
| sma_trend | +18.79% | 93.62% |
| price_trend | +35.62% | 93.39% |
| donchian_breakout | +34.86% | 88.76% |
| rsi_pullback | -0.34% | 45.21% |
| bollinger_revert | -18.02% | 72.68% |
| bullish_engulf | -15.15% | 63.72% |
| hammer_rebound | +26.67% | 52.05% |
| risk_scaled_trend | +18.88% | 57.19% |

The complete source/equity/action evidence contains 17,864 additional SHA-linked decisions in `expert_teachers/`. These fixed-rule returns demonstrate historical profitable examples that BEAN can study; they do not prove a neural selector improves on those rules.

## Portable recovery check

The first GitHub job passed all nine original contracts and recovered identical learned-weight hashes, then failed its exact-decimal prediction comparison across CPU environments. The recovery helper now reports the measured numerical difference, requires relative tolerance 1e-5 and absolute tolerance 1e-6 for float32 values, and independently requires the same greedy action. Artifact and learned-weight hashes still must match exactly. An added test rejects changed choices, material prediction errors, and nonfinite values. No simulation code, financial result, selection rule or learned state was changed. Original CI failure: https://github.com/danieloculus0-bot/AIscend/actions/runs/40134373273 .
