# AIscend 🌱

Experimental noncustodial small-bankroll autonomous crypto trading application with Base wallet/Coinbase Advanced adapters, persistent decision history and BEAN-style research. Not a demonstrated profitable trading strategy.

## Current build state

**Reviewed:** 2026-10-22 · **Stage:** experimental/beta. Do not infer live profitability or completed Coinbase fills from green CI.

- [Current GitHub Actions](https://github.com/danieloculus0-bot/AIscend/actions) show the actual current-commit verification results.
- The Windows desktop exposes a synthetic sandbox, Base smart-wallet execution and Coinbase Advanced spot execution.
- The Coinbase Advanced engine handles funded orders, authentication, pending-order reconciliation, and persistent diagnostics. BEAN Core itself is a separate advisory integration; the local adaptive Q policy can contribute executable signals on Advanced.

## Working implementation

- Runnable local trading/dashboard code with exchange adapters and persistent state; synthetic and live execution modes have different risks.
- Historical, forward-candle and cost-aware strategy research, candidate scoring and experimental policy feedback.
- Separate [BEAN](https://github.com/danieloculus0-bot/BEAN) reasoning research may advise the app; BEAN Core does not directly execute AIscend trades.

## BEAN learning research evidence (October 25, 2026)

The BEAN domain-capable DQN and full-state recovery research learner is [merged into BEAN Core](https://github.com/danieloculus0-bot/BEAN/pull/166). AIscend's separate 389-market training work preserved real gradient updates, original candles, immutable decisions, losing outcomes and reversible compact scores, but it **has not established a profitable learned policy**. [Read the evidence publication, failures, pending tests and exact research PRs](docs/BEAN_ADAPTIVE_LEARNING_PUBLICATION_2026-10-25.md). Those AIscend research branches remain unmerged and do not enable funded trading.

## Install and verification

For local Windows setup and paper/synthetic launch instructions, consult the [full archived README](docs/archive/README_2026-10-20_before_refresh.md). Code entry points include `main.py` (live-capable desktop launch) and `web_app.py` (research/dashboard).

Before restarting funded autonomy on Windows, use the **reviewed, CI-tested release commit**, not an unmerged `main` checkout. PR code is not installed by `git pull` while the PR remains open. Get the reviewed 40-character commit SHA from the green [GitHub Actions](https://github.com/danieloculus0-bot/AIscend/actions) run.

From a clean, locally checked-out copy of that exact commit, using the installed Python virtual environment:

```powershell
git rev-parse HEAD
.\.venv\Scripts\python.exe -m scripts.relaunch_check --expected-sha <REVIEWED_40_CHARACTER_SHA>
```

The gate checks the local Git revision and worktree, runs the full installed Python regression suite, then queries authenticated Coinbase account and read-only product-size rules and inspects the existing order journal. It **does not start trading, transfer money, submit an order, or remove an unresolved client-order ID**. Exit code 0 means these *specific* local gates passed, not that a profitable strategy or a real fill was demonstrated. Exit code 2 means relaunch remains blocked. If an order is unresolved, reconcile it explicitly instead of bypassing the blocker.

Only after the gate passes and the installed commit is reviewed should the operator deliberately run `python main.py`, select the live execution rail and start autonomy. The desktop defaults to synthetic. Monitor venue fills, fees, balances and drawdowns. The unattended web runner now stops after five consecutive failed cycles rather than retrying forever.

For a standalone read-only account/journal diagnostic, run `python -m scripts.live_preflight`. The locally running web dashboard also exposes `/api/trading-diagnostics`. Do not publicly expose web trading controls.
## Known limitations

- Green research CI and Windows packaging are **not** proof of realized returns, validated fills or unattended trading safety.
- Backtests and synthetic candles do not establish out-of-sample profitability; performance claims require reconciled real exchange records.
- Wallet authorization and any real funds remain separate from BEAN memory and documentation.

## Next priorities

Prove actual order/fill reconciliation, fee/slippage accounting and reproducible forward evaluation. Keep the experiment and the ERP products independent.

## Documentation

- [Public AIscend demo](https://danieloculus0-bot.github.io/BEAN/aiscend.html)
- [**Original complete README: earlier designs, experiments, full setup and history**](docs/archive/README_2026-10-20_before_refresh.md)

Older milestones have been archived rather than presented as current build capabilities.
