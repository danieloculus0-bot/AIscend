# BEAN balanced imitation: preregistered failure-correction study

Protocol frozen before this candidate runs. Research-only stacked on AIscend draft PR #118 at `77ad305ccd735c1a2286070b41e63c0d591add4b`. No changes to funded trading or BEAN Core.

## Baseline evidence, no rewriting of losses

Verified GitHub Actions [42739409286](https://github.com/danieloculus0-bot/AIscend/actions/runs/42739409286) ran the actual pinned BEAN SpongeDQN learner and supervised behavior cloning on public BTC-USD / ETH-USD candles. Both unweighted students chose **cash on all 155 historical evaluation decisions**, resulting in 0.00% return and 76.13% nominal rule agreement. Both deliberately shifted-label students also chose cash on all 155 decisions. Retained development action labels, seed 7: cash **792**; leader **360**; runner-up **284**. The unweighted students' headline agreement was therefore the *all-cash majority-class baseline*, **not** proof of learning the rule or favorable future performance. The source remains 8,280 historical public OHLCV rows with SHA256 `6600af461bada8b7e345353b3db9399fab1d66e262fbbe00a0f9d6defbf4e963`. All historic outcomes have already been inspected.

## One correction and one explicit negative control

Add an **inverse-frequency weighted cross-entropy imitation arm** to the same real pinned BEAN Core SpongeDQN online network and existing optimizer. With `K=3` and `N` development expert observations, weight for action `a` is `N/(K * count_a)`, calculated **only from the development examples**. Reject any absent action class instead of inventing it. Do not rescale loss using evaluation prevalence. Preserve the unweighted baseline unchanged.

Predeclare only these two new arms:
- `balanced_mentor_bc`: development expert actions from the same already-defined BTC long/cash 200-day/12-month defensive rule, labels translated to cash/leader/runner-up on each historical decision.
- `balanced_shuffled_mentor_bc`: exactly the previously defined cyclically shifted expert target *negative control*, with **its own training-only** class weights and equal gradient budget.

Both receive identical dates, input features, 1405 gradient steps per seed (7/41), four repeated development passes, batch size 16, learning rate 0.0005, delayed t+2 six-hour fill, outcome at t+8, 95 basis points per side modeled costs and identical 1%-volume fill cap. Evaluate *without* any expert intervention on the same frozen 30% **already inspected retrospective window**. Compare to the previous cash, fixed defensive, BTC hold, unweighted imitation and shuffled-label results. Retain all losses. No winner cherry-picking.

## Mandatory falsifiability and diagnostic metrics

Report the frozen evaluation **cash-only baseline agreement**, the BTC-label count, the BTC-entry *recall* (correct BTC selections divided by times the historical teacher selected BTC), **BTC selection precision**, cash-specificity, incorrect ETH rotations, 3-way target confusion, fees, hypothetically executed fills, max drawdown, full event receipt hash, model weights before/after, optimizer/replay/RNG checkpoint and exact restored replay. Undefined denominators are null, never fabricated zeros. A zero-BTC-selecting model must have **0 BTC recall** when any BTC opportunities exist and cannot be called useful just because it matched cash decisions. Distinguish classification agreement from after-fee portfolio returns.

A positive *historical* result or class-recall improvement does **not** establish a profitable strategy, fresh OOS evidence, or reliable exchange fills. Class balancing is a mechanistic hypothesis, not a tuned edge. Report if the negative control performs as well or better. No live orders, account/API keys, money, stablecoin transfers, leverage, auto-merge, Core changes, ledger edits, or model promotion.
