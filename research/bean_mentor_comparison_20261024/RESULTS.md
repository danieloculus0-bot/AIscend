# BEAN matched mentor explanations and retained feedback

BEAN now receives explicit strategy comparisons after outcomes mature: market P&L before and after the delayed fill, paid costs, net result, the strategy mechanism, and advantage over unchanged holding. These are measured accounting explanations; they do not prove a predictive market law or human-like causal understanding.

The worker validates each case and its knowledge cutoff before converting the matched advantage into actual BEAN neural training rewards. Exact input IDs, received reward arrays, receipt chains and past-case inference explanations are retained. Future outcome components are never decision features. Duplicate teaching evidence is rejected within this phase.

## Controlled development comparison

The same source history, seed 41, 1,024 gradient updates, example order, network, replay budget and fee model were used in both arms. Both received the explanatory cases; their only training-reward difference was raw net log return versus advantage over holding. Neither used pattern-memory blending during evaluation. This was April-September 2025 development history already inspected in earlier work.

| Arm | Mean net return across eight accounts |
| --- | ---: |
| matched_raw_reward | -2.45% |
| coached_advantage | -6.24% |
| cash | +0.00% |
| buy_hold | +71.65% |
| sma_trend | +5.54% |
| donchian_breakout | +19.24% |

The coach delivered 4,320 matched starting-state lessons and 47,520 action comparisons. Both actual networks updated their parameters. Coaching changed mean return by -3.79% relative to the matched raw-reward control. It did not improve this financial diagnostic. No winner is selected by this result.

## Learning from the actual student mistakes

After the outcomes matured, 176 actual coached-policy decisions were reconstructed from their exact starting exposure and source prices. Each realized return matched its decision ledger, and all alternative strategies were compared on the same state/path. The net advantage must reconcile as market-exposure advantage minus additional costs. The complete cases and reason codes are in `student_vs_mentor_outcomes.jsonl.gz`; all source teaching cases are in `student_feedback_lessons.jsonl.gz`.

Example from the actual simulated student:

```json
{
  "additional_cost": -0.00975382223373931,
  "best_hindsight_teacher": "hold",
  "decision_at": 1744675200,
  "interpretation": "Matched historical accounting, not a proven predictive cause; hindsight winner is revealed only after maturity",
  "lesson_id": "12fcef151291294cf067f2188a26f7deb0179da54b9160d1612613a01d9b2ec8",
  "market_exposure_advantage": 0.10637470847369776,
  "net_advantage": 0.11612853070743712,
  "outcome_at": 1745366400,
  "reason_codes": [
    "M01_STUDENT_MARKET_LOSS",
    "F02_MENTOR_PAID_LESS",
    "M02_MENTOR_EXPOSURE_GAIN"
  ],
  "student_action": "price_trend",
  "student_market_pnl": -0.024617776626069,
  "student_net_return": -0.03437159885980834,
  "student_paid_cost": 0.00975382223373931,
  "symbol": "LTCUSDT",
  "teacher_net_return": 0.08175693184762878,
  "teacher_rule_mechanism": "Keep existing units; avoids paying for an unnecessary rebalance."
}
```

Those 1,936 further action comparisons were fed into the retained actual Core learner. Its neural update count increased from 1024 to 1728; frames increased from 47,520 to 49,456, and the learned-weight hash changed. The updated model was saved as an explicitly UNSCORED training capsule, preserving optimizer, target, RNG and replay. Its knowledge cutoff is 2025-10-01, so it cannot be used earlier. **No post-consolidation performance evaluation has been performed.** Retaining these updates does not establish that they help.

## Recovery

```bash
python -m scripts.bean_strategy_policy --directory research/bean_mentor_comparison_20261024 --core-dir ../BEAN --policy-id consolidated_after_feedback --as-of 2026-10-25T00:00:00Z
```

The raw-reward and coached pre-feedback policies are also retained. Their scored development returns belong to those exact earlier weights. The unscored continuation has a distinct objective and qualification, preventing an old return from being assigned to the new model.

This is a standalone test adapter using Core revision 887935ae816b4ce6175c6cde54d749f0e5cf3268. It does not upgrade the hosted BEAN memory connector or activate AIscend live trading. All source OHLCV and previous teacher scoreboards remain in `research/bean_strategy_curriculum_20261024/`. The original opened final year was not reused.

READY FOR REVIEW: five additional tests passed locally with actual Core consumption and gradient changes. General understanding, future profit and improvement after consolidation remain unproven.
