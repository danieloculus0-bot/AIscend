# AIscend 🌱

Experimental noncustodial small-bankroll autonomous crypto trading application with Base wallet/Coinbase Advanced adapters, persistent decision history and BEAN-style research. Not a demonstrated profitable trading strategy.

## Current build state

**Reviewed:** 2026-10-20 · **Main:** `57282db74599` · **Stage:** experimental/beta.

- [Windows Build](https://github.com/danieloculus0-bot/AIscend/actions/runs/39489692368) — **Passed** at inspected `main`.

Consult [live Actions](https://github.com/danieloculus0-bot/AIscend/actions) before treating any build as release-ready.

## Working implementation

- Runnable local trading/dashboard code with exchange adapters and persistent state; synthetic and live execution modes have different risks.
- Historical, forward-candle and cost-aware strategy research, candidate scoring and experimental policy feedback.
- Separate [BEAN](https://github.com/danieloculus0-bot/BEAN) reasoning research may advise the app; BEAN Core does not directly execute AIscend trades.

## Install and verification

For local Windows setup and paper/synthetic launch instructions, consult the [full archived README](docs/archive/README_2026-10-20_before_refresh.md). Code entry points include `main.py` (live-capable desktop launch) and `web_app.py` (research/dashboard). Run the test suite before using a live connection.

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
