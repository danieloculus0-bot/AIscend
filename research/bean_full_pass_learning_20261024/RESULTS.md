# Full-pass matured-evidence learning

Every supplied source row participated in at least one actual Core gradient batch before eviction; source-ID coverage is checked, not inferred from frame counters. This is not proof of semantic understanding or profitability. The replay is deliberately only one mini-batch; older examples remain in the immutable corpus and older learned weights are checkpointed. The effect of the changed rehearsal schedule is measured rather than assumed. All dates remain already inspected development history.

| Window | Reward basis | Prior 4096-replay control | Complete-pass schedule | Difference |
| --- | --- | ---: | ---: | ---: |
| 2023 | gross | -9.13% | -3.77% | +5.35% |
| 2023 | net_liquidation | -8.75% | -1.38% | +7.37% |
| 2024 | gross | -5.44% | -9.71% | -4.27% |
| 2024 | net_liquidation | -2.75% | -6.20% | -3.45% |
| 2025 | gross | +17.19% | +5.80% | -11.39% |
| 2025 | net_liquidation | -1.41% | +0.36% | +1.77% |

Seed, architecture and total updates match the controls, while replay size and gradient timing change together. All six complete Core optimizer/target/RNG/replay states independently recovered. No fallback profit, independent predictive edge or competitor superiority is attributed to passing software/coverage checks.
