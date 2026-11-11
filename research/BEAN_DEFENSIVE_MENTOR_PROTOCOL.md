# BEAN defensive-momentum mentor, matched training protocol (development only)

**Frozen before this mentor implementation and evaluation.** Source baseline: AIscend draft PR #116 exact head `0134340ddfeb629a343eb34381c7184ab306f433`. Actual separate BEAN Core pinned `d13ea68fd2d0fd4cd7af1cd297e1fbec75a954fe`. Public Coinbase source SHA256: `6600af461bada8b7e345353b3db9399fab1d66e262fbbe00a0f9d6defbf4e963`, 8,280 BTC/ETH rows, Jan 1 2024 through Oct 31 2026 UTC.

## Motivation and limitations

The previously frozen retrospective evaluation at [GitHub run 42737902395](https://github.com/danieloculus0-bot/AIscend/actions/runs/42737902395) showed:

| Candidate | Retrospective fee-net return | Maximum drawdown |
| --- | ---: | ---: |
| Cash | +0.00% | 0.00% |
| BTC buy/hold | -13.49% | 38.24% |
| BTC 200-day/12-month long-cash | +3.97% | 10.83% |
| BEAN trained seed 7 | -19.13% | 26.84% |
| BEAN trained seed 41 | -60.99% | 65.65% |
| Fixed 16-bar leader rotation | -78.07% | 79.07% |

This positive fixed-rule outcome is **already inspected historical data**, NOT an independently unseen window or proof of an edge. It can generate a learning hypothesis, but reuse of the same historic test period creates development contamination. Any subsequent comparison must be clearly labeled *re-exploration of known retrospective history*. Actual promotion requires fresh elapsed-time prospective evaluations after protocol freeze, plus replication.

## Mentor experiment

- Same synthetic-free **exact public source SHA**, same 70% development/30% retrospective diagnostic chronology, two seeds (7 and 41), four replays of each development period, exactly the same DQN architecture, optimizer, reward accounting, delayed t+2 open fill, t+8 maturation, and 95bps per-side costs.
- **Self-exploring arm (original baseline)**: Same epsilon-greedy selection from BEAN. No changes to its algorithm or reward function.
- **Mentor arm (new)**: During development ONLY, BEAN observes the outcome of decisions from the predeclared BTC long/cash rule: hold/buy BTC unless BOTH trailing 12-month BTC return AND BTC versus 200-day SMA are negative, otherwise cash. This feeds genuine fully matured fee-net rewards through real `SpongeDQN.observe`. On evaluation, BEAN must decide **independently with epsilon=0**, with the mentor removed. This is genuine model training, **not** replacing the model's inference with a hardcoded teacher.
- **Placebo arm**: Same predetermined teacher decisions shifted by a deterministic cyclic offset within the training period, thereby breaking contemporaneous regime alignment. It must use identical number of observations and updates, and the same costs and learning architecture. No future information available to the deployed agent; the shifted teacher is an explicit *development-only* negative control.
- Re-run all arms on precisely the same public historical source, two seeds, 4 passes, with individual immutable source and decision hashes. Retain full training-state checkpoint, optimizer, target, RNG, replay and all failures, including models losing to cash.
- Evaluate identical BTC/ETH portfolio paper positions over the 30% latter, already-inspected portion; compare mentor/self/placebo, untrained DQN, fixed long/cash, BTC hold, 16-bar momentum, cash. No claims of independent forward profitability from any return on this already-known period. Keep confidence intervals diagnostic only.
- Exclude future information in policy input. The mentor action is determined from historical closed candles up through the decision timestamp, and each fee-net reward is fed back only after maturation. No model updates on the evaluation period.
- **No trade placement, account/API keys, funds, leverage, shorting, stablecoin transfers, BEAN Core changes, automatic model promotion, auto-merge or mutable existing ledgers.**

This is an experiment about whether BEAN **can internalize a historically useful disciplined slow-regime behavior**. The fact that a fixed rule was positive in one selected window does not mean it will be again or that the neural student will do better.
