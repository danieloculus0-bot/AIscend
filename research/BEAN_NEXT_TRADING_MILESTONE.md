# Next push: preserve useful strategy behavior and measure added learning

The objective is a profitable adaptive trader with independently demonstrated
advantages. Current evidence does not establish that objective or a rank against
commercial competitors. More passes through already inspected history cannot,
by themselves, establish either claim.

## Concrete defects addressed in this experiment

At the prior selected checkpoint, a zero-exposure observation gave cash a value
of 0.051843 and holding cash a value of -0.139852. Their actual modeled outcomes
are both exactly zero. Independent action heads were learning different values
for economically equivalent choices. The new factored arm predicts two market
return components and uses funded accounting to value every action, so equivalent
exposures have identical modeled values. This fixes consistency, not forecast skill.

The earlier replay learner ingested 47,520 action labels, retained 4,096, and did
not update during ingestion. Repeated gradient updates then sampled that retained
subset. Raw labels also repeat the same market episode across 11 actions and
three starting exposures. The new experiment reports distinct asset/period
outcomes, complete available replay, exact sampled-entry counts and evictions.
It compares limited and complete replay using the same input order, architecture
and update budget. Full states are stored externally with Journal hashes when
the old 16 MB expanded capsule limit would otherwise prevent retention.

The parent branch advanced during this work: 75bd175c added bounded chunked
teaching/gradient digestion. That implementation is reused unchanged as a fourth
comparison arm. Coverage is tracked by exact source IDs across replay eviction,
so having a gradient step for each cohort is distinguished from sampling each
individual row. The initial three-arm run remains separately archived; the
four-arm result is stored under `bean_economic_values_chunked_20261024`.

## Locked comparison

Three chronological development splits compare raw action values with limited
replay, raw action values with complete replay, and factored market forecasts.
The factor forecast is additionally checked against zero and training-mean
predictions. All use the same $1,000 funded allocation, prices, costs and delayed
fills. A fixed expert is chosen using training-period returns only. Learned
overrides are enabled for the later diagnostic only if the proposed overlay
improves both earlier development folds and its forecasts beat the past-mean
prediction baseline on both. Otherwise the explicit expert remains in control.
Any profit from that fallback belongs to the expert; it is not a neural gain.

The eight surviving candle instruments are a mechanics test, not a substitute
for the broader universe. The earlier 1,141-asset price collection and full
strategy atlas remain archived in parent branches. Their availability and price
definitions must be respected when extending this model beyond OHLCV coverage;
missing candles must not be invented.

## Following qualification work

1. Verify corrected accounting, actual sample coverage, complete recovery and
   measured incremental results. Preserve every failed candidate.
2. Extend to point-in-time market eligibility across the available Coinbase
   universe, including stale/delisted histories and missing-data failures.
3. Add genuinely new chronological evidence and held-out instruments. Current
   dates are development evidence and cannot become unseen tests again.
4. Compare fixed experts, the learned controller and reproducible public model
   baselines on identical capital, dates, quotes, fees and risk measures. Public
   marketing returns from different conditions are not an audited comparison.
5. Evaluate forward paper execution with observable spreads, delays, fees and
   failed fills. Narrative inputs require first-seen timestamps and explicit
   availability; today's headlines cannot be backdated.

Metrics include net equity, costs, turnover, time invested, daily-close drawdown,
forecast error versus simple controls, and incremental return over the selected
expert. A higher update count, positive segment or a passing software test is not
proof of improved trading. Competitor superiority requires independently
comparable evidence; no such claim is made by this experiment.

Primary research context: Bailey et al., The Probability of Backtest Overfitting,
https://scholarworks.wmich.edu/math_pubs/42/ . Fujimoto et al., Off-Policy Deep
Reinforcement Learning without Exploration,
https://proceedings.mlr.press/v97/fujimoto19a.html . The latter is general offline
RL context, not a diagnosis of bootstrapping error here: these adapters use
gamma=0 immediate outcome regression with counterfactual action labels.

## BEAN context acknowledgement

BEAN CONTEXT ACK v1: hosted status/older recall, current gatekeeper, the pinned
Core learner at 887935ae816b4ce6175c6cde54d749f0e5cf3268, and the complete
dc6403a2 development receipts were checked. Reuse the actual SpongeDQN, Journal,
Account, Features and DecisionLedger; do not replace earlier failures. GitHub run
40135530917 passed 15 prior contracts at dc6403a2. New changes are a separate
research adapter and explicit expert-first experiment, with no live runtime
activation or new behavior restrictions outside the experiment.
