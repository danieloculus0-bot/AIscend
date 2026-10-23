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

## Install and verification

For local Windows setup and paper/synthetic launch instructions, consult the [full archived README](docs/archive/README_2026-10-20_before_refresh.md). Code entry points include `main.py` (live-capable desktop launch) and `web_app.py` (research/dashboard).

On the installed Windows checkout, **do a read-only Coinbase Advanced preflight before running funded autonomy**:

```powershell
cd C:\Users\Daniel\AIscend
git pull --ff-only
.\.venv\Scripts\python.exe -m scripts.live_preflight
.\.venv\Scripts\python.exe main.py
```

Preflight performs authenticated status reads and checks the local order journal. It **never places trades**. Exit code 0 means the read-only readiness checks passed; code 2 means it reported a blocker. A green preflight does not prove an exchange fill. Inspect [live trading diagnostics](http://127.0.0.1:8000/api/trading-diagnostics) from the locally running web dashboard when available. No public web endpoint should be exposed without the app's access controls.

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
