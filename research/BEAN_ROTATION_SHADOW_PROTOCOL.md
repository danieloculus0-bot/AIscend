# BEAN cross-asset rotation learning, research-only protocol

**Status:** Unvalidated engineering experiment, not a trained live policy, exchange order, or independent profitable backtest. The only market observations permitted are synthetic or lawful public six-hour candles; do not publish authenticated account balances, secret keys, connected exchange history, or user financial data.

## BEAN CONTEXT ACK v1

- **Task:** Create one isolated AIscend shadow-learning experiment for defensive exits, holding, high-momentum rotation, and recovery reentry.
- **Existing systems checked:** AIscend `AGENTS.md`, README, archived adaptive-learning results; PRs #111, #110, #109, merged #107, closed/unmerged #67, and newest #112-114. BEAN `docs/AI_GATEKEEPER.md`, `docs/SOFTWARE_AUDIT_SKILL.md`, `bean/optimization/sponge_dqn.py`, and `bean/learning/runtime.py`.
- **Reused assets:** Actual BEAN Core `SpongeDQN`, pinned at Core commit `d13ea68fd2d0fd4cd7af1cd297e1fbec75a954fe`, original Git blob `08abcf5039f7f440fa936aba31ef9a3a2b96f153`. Import from an explicitly separate Git checkout. No imitation DQN and no hosted adapter training.
- **Ledger/scoreboard reviewed:** Published `docs/BEAN_ADAPTIVE_LEARNING_PUBLICATION_2026-10-25.md`; `research/bean_retained_teaching/run-40167005185/RESULTS.md` and retained teaching protocol on archived PR #96. Archived research's selected cash baseline beat its developed model candidates. Original archives are not changed.
- **Evidence:** The new deterministic unit tests exercise only a test double for the BEAN learner. A production Core checkout and exact-head Actions are required before claiming real neural training or checkpoint recovery. Source-only GitHub verification and old green CI do not establish that claim.
- **Unknowns:** New, genuinely unseen real market periods; fee-net generalization; public candle executable liquidity; actual account cashflow; whether GitHub Actions will execute at this new head.
- **Minimum change:** A new standalone shadow simulator, its synthetic tests and this protocol; no live AIscend entrypoints changed.
- **Runtime or behavior-policy changes:** None in funded AIscend or BEAN Core.

## Action economics

There are **three asset targets**, not redundant “personality” switches: `cash`, `leader` (strongest trailing 16-bar return), and `runner_up`. These targets resolve differently depending on the *existing* held asset. The possible resulting events include `ABSTAIN`, `HOLD`, `ROTATE`, `DEFEND`, `REENTER`, and `UNFILLED`. The target ranks are calculated only from completed bars, and the neural learner receives a nine-number observation (two assets' short and long trend and historical volatility, plus current holding one-hot). It never receives future prices, bar indexes, calendar IDs, private credentials, or exchange client objects.

USD cash is a research accounting placeholder. It is **not** an actual USDC trade, stablecoin depeg model, withdrawal, or wallet transfer.

The decision is made after candle `t` closes, hypothetical execution is no earlier than **open `t+2`**, and the reward is available only after **close `t+8`**. The next decision occurs at `t+8`. Every realized target change pays **90 basis points in modeled fees plus 5 basis points for half of a 10-basis-point spread per side**, 95 bps total per side. Existing inventory incurs no new transaction fee when held; mark-to-liquidation NAV conservatively reserves a potential exit cost. The modeled order must fit within 1% of the relevant bar's positive volume on both legs, or the entire requested switch is unfilled and the original position remains outstanding. Positive volume does not prove real fillability.

## Evaluation and audit gates

Train the actual Core learner on the first 70% of the chronology, after a 16-bar history warmup, using two fixed seeds (7, 41) and four repeat passes through the *same* development period. Repeating history yields more gradient updates, **not** more independent observations. New policy weights, true gradient update count, learning events, original source hash, checkpoint state and verification digests must be retained. A restored learner must reproduce exactly the same decision/event hash. The last training outcome must not cross into the frozen trailing 30% evaluation period. No evaluation observations are fed to training.

Compare identical starting capital ($1,000 per independent paper arm), dates, target-specific fills, and fees against `cash`, fixed-first-asset buy-and-hold, fixed strongest-momentum rotation, and fixed trend-exit; also compare the same seed's **untrained** BEAN neural weights. The synthetic block-bootstrap interval is merely a dependence-sensitive diagnostic, not a confidence claim for future market performance. Retain all losing trades, declined orders and decision features, plus immutable-ish SHA-linked JSONL event archives. There is no external memory component, so a shuffled-memory placebo is **not applicable** here; an added retrieval layer will require that placebo before any benefit claim.

If a candidate loses to cash, exhibits unrepresentable reward/positions, has unequal training budgets, lacks actual BEAN Core execution, or lacks independent prospective out-of-sample fee-net results, it stays experimental. No model auto-promotion, funded execution, automated rotation, release packaging, wallet authorization or immutable ledger reset.

## Reproduce without network access in the training process

```bash
# Clone/pin independently in an authorized environment BEFORE running this test.
git -C /path/to/BEAN rev-parse HEAD
# Must print d13ea68fd2d0fd4cd7af1cd297e1fbec75a954fe
python -m unittest discover -s tests -p 'test_bean_rotation_shadow_study.py' -v
python -m research.bean_rotation_shadow_study \
  --bean-core /path/to/BEAN \
  --synthetic-fixture \
  --output artifacts/bean-rotation-shadow-first-run
```

A real public two-product source can instead be passed using `--source-jsonl`. Rows must carry `symbol`, `ts` or `open_time`, `open`, `close`, and `volume`, optionally `high`/`low`, all complete, contiguous and timestamp-aligned. The source file is never included in published source or CI logs without a separate public-data provenance audit. Outputs are new-directory-only; never overwrite an existing observation ledger. **A successful synthetic run proves only that the BEAN Core learns from synthetic state/reward transitions and recovers its checkpoint, not that it will earn money.**
