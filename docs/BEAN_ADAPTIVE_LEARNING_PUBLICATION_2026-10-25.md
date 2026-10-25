# BEAN + AIscend: reproducible adaptive-learning evidence (2026-10-25)

Publication scope: source-linked research, including negative results. **No profitable autonomous trader, independently verified forward return, or production deployment is claimed.** This document describes specific recorded experiments, not the capabilities of the Python trading release.

## Published Core integration

BEAN [PR #166](https://github.com/danieloculus0-bot/BEAN/pull/166) was merged into Core `main` as [d13ea68fd2d0fd4cd7af1cd297e1fbec75a954fe](https://github.com/danieloculus0-bot/BEAN/commit/d13ea68fd2d0fd4cd7af1cd297e1fbec75a954fe). It ports the original domain-capable SpongeDQN research engine with actual gradient updates, target network, experience replay, and complete compressed restoration of optimizer, weights, RNG, replay and update counters. The recorded five PR workflows passed at source head `887935ae816b4ce6175c6cde54d749f0e5cf3268`. The library is optional and does not execute live AIscend trades or connect the hosted BEAN note adapter to model training.

## Recorded market outcomes, including failures

[AIscend PR #94](https://github.com/danieloculus0-bot/AIscend/pull/94) attempted 389 currently eligible Coinbase USD products and retained 392,099 real daily OHLCV candles. Its wide model received 190,648 historical source rows, and complete training state was recovered. In the April-October 2026 all-USD historical audit, immediate DQN returned **-2.03%**, linked DQN **-4.00%**, fixed training-selected buy/hold **+1.58%**, and the unchanged random network **+7.55%**. Read the [full six-cohort result matrix](https://github.com/danieloculus0-bot/AIscend/blob/experiment/bean-wide-multistep-20261024/research/bean_wide_multistep_capital_20261024/RESULTS.md). The positive random-network return is *not* learned profit. The selected expert and both DQN models trailed it in that audit.

[AIscend PR #95](https://github.com/danieloculus0-bot/AIscend/pull/95) verified an **11.17x** component-level speedup for repeated teaching generation against the frozen original source. It preserved byte-identical teaching rows and decisions plus complete identical Core learner states. Four fixed-seed candidates used all 151,728 eligible training rows, performed 2,371 gradient updates each and recovered fully. Their internal selection returns ranged from **-4.16% to -7.48%**, so the compact SQL selector chose cash. The original source histories, all decisions, losers, checkpoint files and compressed pair-code mapping remain on that research branch. [Detailed score and recovery receipts](https://github.com/danieloculus0-bot/AIscend/blob/experiment/bean-fast-ledger-20261024/research/bean_fast_ledger_20261024/RESULTS.md).

These current-survivor histories were inspected in prior research, even if individual asset IDs were excluded from gradients. Backtests on them are **development evidence, not untouched independent forward validation**. Simulated execution assumes current venue metadata and 1% per-side costs, not actual fills.

## Next experiment and falsifiable gates

[Draft PR #96](https://github.com/danieloculus0-bot/AIscend/pull/96) compares identical-budget grouped and shuffled raw rewards against matched long-versus-cash advantage teaching at seeds 7 and 41, using the actual Core learner. It also tests a bounded, source-linked 16-regime memory with same-asset exclusion, retrieval consumption, an exact memory-only control and label-shuffled placebo, plus mandatory negative-case retention. The [registered protocol](https://github.com/danieloculus0-bot/AIscend/blob/experiment/bean-retained-teaching-20261024/research/BEAN_RETAINED_TEACHING_PROTOCOL.md) was written before the new policy evaluations.

A follow-up source change adds finite-input rejection to prevent NaN/Infinity from reaching the matched-memory engine. New contracts reject future-labelled lessons and tampered source archives. The tests and full experiment must run at their exact latest revision; a source commit alone does not prove they passed.

A useful gain requires a trained model to improve against **matched fixed-rule, cash, untrained, memory-only and label-shuffled controls**, with all net fees and drawdowns counted. Only a *later*, preregistered, truly new forward period can justify an independent profitability claim. Losses and inconclusive trials stay in the ledger; repeat evaluation does not create new evidence or reward credit.

## Exact-head regression result

At research head `16ca54989bc9ae9ba4a5f24876b916a7a322a508`, all four pull-request workflow suites passed: [retained learning and source integrity](https://github.com/danieloculus0-bot/AIscend/actions/runs/40167008085), [cached search](https://github.com/danieloculus0-bot/AIscend/actions/runs/40167008031), [broad multi-step market contracts](https://github.com/danieloculus0-bot/AIscend/actions/runs/40167008116), and [strategy-teaching contracts](https://github.com/danieloculus0-bot/AIscend/actions/runs/40167008030). The first version of the newly added archive test failed at an earlier test-only revision; its incorrectly escaped JSONL separator was fixed, not erased. Passing the four contract suites verifies engineering behavior, **not** the 389-market full research study or subsequent economic outcomes. The [full push-triggered study](https://github.com/danieloculus0-bot/AIscend/actions/runs/40167005185) is separately tracked and must finish and archive its real results before any new market-performance conclusion is reported.

## Completed retained-memory study

[Full GitHub study run 40167005185](https://github.com/danieloculus0-bot/AIscend/actions/runs/40167005185) finished successfully and its append-only source archive was committed to [`research/bean_retained_teaching/run-40167005185/RESULTS.md`](https://github.com/danieloculus0-bot/AIscend/blob/experiment/bean-retained-teaching-20261024/research/bean_retained_teaching/run-40167005185/RESULTS.md). It consumed 151,728 historical source rows and preserved 75,864 matched lessons, 2,048 active-memory cases, six neural training variants, complete decision logs and losses. The pre-audit compact selector chose **cash**, not a trained BEAN policy.

For the historical asset identities withheld from gradients, seed-41 paired-advantage + genuine memory returned **-9.04%** versus **-17.18%** for the *same neural weights with shuffled memory labels*. This is an 8.14 percentage point relative improvement in that one comparison, but both arms lost money. On the historical all-market audit, actual memory returned **-17.87%** versus **-17.37%** for shuffled placebo, so the advantage did not generalize across cohorts. Memory-only returned -7.70% on withheld assets, while cash remained 0%; no neural learner produced positive absolute returns. The six paired diagnostic models reduced repeated training-case MSE; those panels are not independent holdouts.

**Interpretation:** Evidence of real retrieval consumption, checkpoint retention and an interesting asset-cohort difference, not evidence of robust positive return, real exchange profitability, general causal reasoning, or guaranteed future transfer. Historical selection and audit windows had been examined by prior development. Preserve these losses and do not auto-promote.

## Release boundary

- Core learner: integrated to BEAN main, research-capable, optional.
- AIscend PRs #94, #95 and #96: research branches, **not** merged to live-capable `main`.
- Coinbase account reconciliation and stale-history safety integration: separately pending review in [AIscend PR #93](https://github.com/danieloculus0-bot/AIscend/pull/93).
- No trading account, credential, funded order, model auto-promotion or live configuration is changed by this publication.

The compressed ledger is a reversible index over immutable original records. It is not a replacement for price, decision, return, or model evidence.
