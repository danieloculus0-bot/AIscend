# BEAN broad Coinbase multi-step experiment

Historical current-survivor research. Later periods and held-out base assets were excluded from this model training, but these histories have appeared in earlier research. There is no independent forward-performance or market-rank claim. All losses remain in the SQL index and original decision ledger.

Attempted 389 quote markets; 392,099 actual daily candles. Funded USD portfolios use 389 markets, including 71 base assets withheld from all gradient inputs. USDC histories are retained but do not receive an assumed USD conversion. Every portfolio below starts with one actual modeled $1,000 split across its entire stated cohort; these are alternative simulations, not simultaneous accounts. Current metadata and missing history can prevent trades.

Teaching rows: 190,648; nonterminal: 182,728. Both trained Core models sampled every supplied row. Full optimizer, target, replay and RNG states recovered exactly. The new 39-input trade-state model uses the same verified Core, not incompatible weights from the older 47-input task.

| Period | Cohort | Cash | Buy/hold | Frozen expert | Untrained | Immediate DQN | Linked DQN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| validation | known_assets | +0.00% | -49.84% | -49.84% | -1.14% | -17.50% | -17.90% |
| validation | withheld_assets | +0.00% | -45.31% | -45.31% | -2.11% | -20.27% | -22.52% |
| validation | all_usd_assets | +0.00% | -54.57% | -54.57% | -1.32% | -18.46% | -18.92% |
| historical_audit | known_assets | +0.00% | +3.14% | +3.14% | -0.34% | -5.50% | -4.98% |
| historical_audit | withheld_assets | +0.00% | +0.26% | +0.26% | -1.70% | -4.42% | -2.34% |
| historical_audit | all_usd_assets | +0.00% | +1.58% | +1.58% | -0.59% | -5.16% | -4.37% |

The selected expert is **buy_hold**, chosen once from training-only net returns. Learned decisions retain action values and a matched reference action using identical initial account, fill, costs and outcome. The reward ledger reconciles pre-fill market movement, post-fill movement, paid costs and exit-reserve changes; this is mechanical explanation, not evidence of semantic or causal understanding.

Missing marks are conservatively zero until an actual candle returns. There are no interpolated candles or retroactive exits before gaps. Unliquidatable terminal dust is not credited as cash. Current listing/order metadata are not historical venue rules or account permissions. One percent per-side execution costs are a stress assumption, not measured fills.

The next independent test must collect new observations after model selection and keep every candidate frozen. Repeated historical runs are development, even if they become profitable. No live trading was enabled.

