# BEAN economic consistency and replay coverage

This is an already-seen, eight-symbol development ablation. It cannot establish best-on-market performance or independent profitability.

Raw labels: 47,520. Distinct asset/period outcomes: 1,440. The raw labels repeat actions and starting exposures; they are not that many independent market events.

| Fold | Training-selected expert | Expert return | Raw 4096 | Parent chunked 4096 | Raw full replay | Factored values | Economic overlay |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2023 | hammer_rebound | -2.66% | -9.72% | +6.65% | -1.73% | -19.95% | -23.72% |
| 2024 | sma_trend | -25.52% | -7.86% | -3.84% | -9.28% | -20.15% | -19.81% |
| 2025 | hammer_rebound | +4.00% | -7.21% | +0.33% | +4.68% | +13.72% | +6.55% |

The predeclared override gate enabled learning overrides: **False**. The final development expert-first policy used **unchanged_expert**, ending with **$1040.01 from $1,000**. When an expert fallback earns money, its return is explicitly attributed to the expert, not to learning.

The factor model predicts two market log-return components. Exact funded trade accounting supplies costs and all 11 action values; cash versus holding cash must match and trades with identical exposures cannot acquire different values from their names. This fixes structural inconsistency, not predictive skill. Forecast MSE is compared with zero and training-mean forecasts.

The raw-limited and raw-full arms share the same examples/order/network/updates. Coverage receipts report how many inputs were evicted before training and how many available entries were actually sampled. The complete Core training state (including full replay, optimizer, target and RNG) is hash-linked through external gzip files because the original capsule has a 16 MB expanded-size limit. Every saved state was independently recovered.

All earlier failures are preserved in parent branches. No old final year was reopened, no live order was placed, and no result here is called an unseen competitor benchmark. Broader point-in-time asset coverage and genuinely new forward evaluation remain necessary.
