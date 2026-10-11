# AIscend x BEAN functional audit (2026-10-10)

Scope: source inspection and GitHub CI only. NOT a live wallet audit, authenticated exchange audit, or proof of trading profitability.

## Confirmed architectural findings

| Subsystem | Finding | Disposition |
|---|---|---|
| Portfolio rotation | Existing AdvancedSpotEngine only exits scored holdings below a fixed negative signal threshold. No cost-aware rotation into a stronger asset. | Opportunity-cost review now recorded, execution not yet connected. |
| Held-asset coverage | A holding absent from the current Coinbase research sample is absent from weakest-held selection. | UNKNOWN signal is now separately visible in rotation reviews; requires live telemetry. |
| Capital utilization | New buys generally require idle funds in a quote currency. | No funded sell-and-buy atomic rotation implemented. |
| Score semantics | Fallback via 'or' could discard genuine zero signals, and routing used raw instead of fused score. | Corrected in pending PR. |
| BEAN freshness | AIscend's embedded BeanMemory is not BEAN Core's current reasoning engine. | BEAN Core checkout + provenance adapter added; runtime default not yet replaced. |
| New listings | Listing Sentinel already adds a 6-hour decaying ranking premium. | Historical launch analyzer added; premium not validated as profitable. |
| Simulation | Existing strategy lab uses synthetic returns and arbitrary penalty assumptions. | Added real Coinbase 1-hour, 4-asset walk-forward challenge. |
| Execution realism | Market order previews exist on Coinbase, but backtest costs are estimates; no recorded bid/ask depth, partial fills, latency or real order-size constraints. | Historical returns are provisional; never use as live-profit evidence. |
| Level 1 | $25 to $100,000 is 4,000x, or about 12 doublings. | Report target honestly. A failed challenge is a failed challenge, not a reason to relabel a cherry-picked result. |
| Main release | All code changes currently live on a PR branch. | Require green historical CI and Windows tests, then deploy Python venv updater. |

## Engineering acceptance gates

1. Historical Coinbase data must have timestamps, source and nonempty rows; no fake fallback.
2. Strategy selection sees training bars only. Out-of-sample holdout is reserved for evaluation.
3. Compare challenger versus cash and a BTC buy-and-hold baseline after estimated costs.
4. Reject inferred certainty: missing evidence remains unavailable, not a sell.
5. Report total net liquidation, max drawdown, trades and whether the $100,000 level was really attained.
6. Include new-listing cohorts in future research with executable price and volume data; do not claim the 4-liquid-asset baseline covers token-launch behavior.
7. Follow-up: independent replays over multiple nonoverlapping dates/coins, commission tier and order-size stress tests, delisting/survivorship controls, and paper-forward validation.
8. Promotion to live trading requires an explicitly tested capital rotation path with exchange minimums, inventory refresh, failed-order handling and independent risk limits.

This is an incremental code audit. It does not demonstrate that any tested method can achieve Level 1, and does not claim to have run a live trading optimization.
