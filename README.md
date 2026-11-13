# AIscend 🌱

Experimental noncustodial small-bankroll autonomous crypto trading application with Base wallet/Coinbase Advanced adapters, persistent decision history and BEAN-style research. Not a demonstrated profitable trading strategy.

## Current build state

**Reviewed:** 2026-10-22 · **Stage:** experimental/beta. Do not infer live profitability or completed Coinbase fills from green CI.

- [Current GitHub Actions](https://github.com/danieloculus0-bot/AIscend/actions) show the actual current-commit verification results.
- The Windows desktop exposes a synthetic sandbox, Base smart-wallet execution and Coinbase Advanced spot execution.
- The Coinbase Advanced engine handles funded orders, authentication, pending-order reconciliation, and persistent diagnostics. BEAN Core itself is a separate advisory integration; the local adaptive Q policy can contribute executable signals on Advanced.

## Working implementation

**Version:** 0.17.0  
**Platform:** Windows x64 + browser via GitHub Codespaces  
**State:** Synthetic desktop sandbox + Coinbase live desktop mode  
**Live-money execution:** Base smart-account swaps and Coinbase Advanced spot orders are implemented
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

Build artifacts include that version automatically.

## Naming

The final product name has not been chosen.

The visible application name can be overridden without refactoring the code:

```powershell
$env:AUTOCAPITAL_APP_NAME="New Name"
python main.py
```

The executable/build artifact name can also be overridden:

```powershell
$env:AUTOCAPITAL_BUILD_NAME="NewName"
.\build.ps1
```

## Multi-market lanes

AIscend exposes four market lanes from first launch:

- **Crypto**: live autonomous execution is wired today through the CDP smart account.
- **Predictions**: public market discovery is live and normalized into the opportunity scanner. Autonomous execution remains locked until a supported authenticated trading adapter is connected.
- **Stocks & ETFs**: the lane is present and research-ready. Autonomous execution remains locked until a supported brokerage API is connected.
- **Options**: the lane is present and research-ready. Autonomous execution remains locked until a supported options API is connected.

The application does not pretend an execution connector exists when one has not been implemented. Research and execution remain separate capabilities.

## Next major milestone

Add more social/event adapters, learn signal weights from realized trade outcomes, and add independent probability models plus authenticated execution adapters for prediction markets, stocks, ETFs, and options.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the current component boundaries and invariants.


## BEAN learning loop

Version 0.11 adds BEAN as the learning spine behind the research layer.

```text
raw market / social / news evidence
        |
typed claims
OBSERVATION / INFERENCE / HYPOTHESIS / PREDICTION
        |
contradiction retention
        |
multi-horizon prediction
        |
future market outcome
        |
prediction grading
        |
source / signal trust update
        |
next decision
```

BEAN does not erase disagreement. Technical and human evidence can coexist as contradictory claims until later outcomes provide evidence. Trust is learned from realized outcomes with Bayesian shrinkage so tiny samples do not become overconfident.

## Coinbase asset breadth

The research layer now discovers active Coinbase public USD and USDC products and scans them in rotating batches. This gives AIscend broad market awareness without making hundreds of public API calls every few seconds. The dashboard reports the number of discovered products, how many were recently scored, and the strongest current candidates.

This is deliberately separated from execution. The current funded CDP smart-account rail is still Base USDC/WETH, so broader Coinbase assets can be researched and learned from before their execution adapters exist.


## Signal reliability and memory compression

Version 0.12 hardens the two systems exposed by the first live BEAN run:

- Human Weather now has redundant public-source paths instead of silently returning zeros when one provider blocks a cloud runtime.
- GDELT remains the primary global-news source, with Google News RSS as a fallback.
- Reddit JSON falls back to subreddit RSS while it remains available, and Bluesky public search provides an additional social signal source.
- Feed health and fallback diagnostics are visible in the dashboard.
- BEAN compresses repeated observations from the same epistemic object into persistent rollups rather than counting every 60-second refresh as a new claim.
- Predictions and resolved outcomes remain separate records and are never compressed away.


## Signal maturity

Version 0.13 keeps raw Human Weather readings intact while adding the missing context around them.

- Sentiment now reports coverage: how much of the collected sample actually matched the directional lexicon.
- Social confidence combines coverage, sample size, and source diversity.
- Human-signal quality is tracked separately from directional sentiment.
- The research fusion uses maturity to control influence without hiding raw +1 / -1 observations.
- BEAN now grades news, social, politics, crowd, Fear & Greed, technical, human, and composite signal families separately as outcomes resolve.


## Coinbase Advanced multi-asset spot rail

Version 0.13 also adds an optional Coinbase Advanced execution rail. It is separate from the funded Base smart wallet.

- Base smart wallet remains the default live rail.
- Coinbase Advanced can be connected with a dedicated Advanced Trade API key.
- The Advanced engine ranks currently-scored account-eligible Coinbase spot products and can buy the strongest qualifying USD/USDC setup.
- Held products are re-evaluated and can be sold when their signal deteriorates.
- No borrowing or short selling is used by this spot engine.
- All first-class market lanes are enabled in the Market Arsenal: crypto spot, predictions, stocks & ETFs, futures, perpetuals, and options. Research readiness remains separate from execution connectivity.


## AIS-0 Tamagotchi behavior

Version 0.14 keeps the original intentionally terrible pixel creature and gives it dead-simple emotional behavior. It still bobs and blinks, but now reacts to buys, sells, wins, losses, thinking, errors, and bearish market states. If the live research brain is bearish while the runner is active, AIS-0 may perform a tiny tactical shit and complain about the market. Mood text rotates through short, mildly profane quips without changing trading logic.


## Wallet bridge

Version 0.15 adds direct USDC plumbing between the Base smart wallet and the Coinbase Advanced account.

- Base to Advanced asks Coinbase for a Base USDC receive address and sends USDC from the CDP smart account.
- Advanced to Base sends USDC from the Coinbase App account to the existing Base smart-account address.
- The bridge panel shows both balances plus API permission state.
- Coinbase transfer/receive permissions are checked separately from trade permission.
- Decision Journal is now fixed-height and scrollable.
- Human Weather, BEAN Memory, Coinbase Asset Universe, and Market Arsenal are collapsible to keep the dashboard compact.


## Remote monitor

The Flask app binds to `0.0.0.0:8000`, but non-local requests are now restricted to the read-only monitor surface: `/monitor`, `/api/monitor/status`, and `/api/candles`. Trading, bridge, credential, runner-control, and full-dashboard routes return 403 to remote clients.

Remote monitor access requires `AISCEND_REMOTE_TOKEN`. The included Windows helper `scripts/start_remote_monitor.ps1` generates a strong process-local token when one is not already set, prints a home-LAN URL when available, prints a Tailnet URL when Tailscale is installed, and then starts the app. The token is moved into request headers after the monitor page loads so it does not remain in browser history/API URLs.

For access away from home, put the read-only monitor behind a private Tailnet or an HTTPS tunnel. Do not expose the unrestricted local dashboard directly to the public internet.


## Universal Coinbase spot scan + Listing Sentinel

AIscend no longer limits Advanced research to USD/USDC products. The public
Coinbase spot universe is scanned across every quote currency returned by the
exchange, including small-cap and newly-added tokens. There is no reputation or
market-cap allowlist; execution eligibility comes from the connected Coinbase
Advanced account plus market/liquidity checks.

The Listing Sentinel polls public Coinbase product metadata on roughly a
one-minute cadence and persists a baseline in the Advanced state database. It
records newly-visible product IDs and public trading-state changes such as
auction, limit-only, trading-disabled and full-trading transitions. This is
public-data monitoring only.

Fresh products are researchable immediately. The asset scanner no longer waits
for six hours of 5-minute candles before admitting a product. New/unseen
products receive scan priority, and a recent public full-trading/listing event
can temporarily boost the Advanced opportunity score while normal spread and
volume gates still apply.

The Advanced Decision Journal now records every cycle outcome, including
exchange/API execution failures. The browser journal combines Base and Advanced
decision histories and labels each row by rail. A running rail can no longer be
silently replaced by starting another rail; stop the active runner before
switching.


## v0.16 repo audit: linked capital system

The live rails now behave as one experiment instead of two unrelated games.

- The original Base `live_starting_value` remains the canonical bankroll baseline when capital moves to Coinbase Advanced, so the $25 experiment does not reset to the destination balance.
- The original game clock and milestone metadata follow the bankroll across rails.
- Advanced execution uses the existing Base BEAN database as shared learning memory, so switching execution rails does not create a second brain.
- The browser detects when the selected rail is empty and the other live rail holds spendable capital.
- A successful Base/Advanced bridge move changes the selected execution rail and, when autonomy is already running, stops the old runner and restarts it on the destination rail.
- The Decision Journal combines Base and Advanced rows and labels the rail.
- SQLite live state uses WAL mode plus a busy timeout so the background runner, browser dashboard, and remote monitor can read/write without needless lock collisions.
- The read-only remote monitor follows the funded rail when the runner is stopped.

## Execution and tax audit trail

Live executions and bridge transfers write an append-only reconciliation trail under the AIscend application-data directory:

- canonical yearly JSONL
- review-friendly yearly CSV
- UTC timestamps
- trades kept distinct from internal Base/Advanced transfers
- provider order/transaction IDs when available
- decimal quantities preserved as text
- credential-like fields redacted from stored provider responses

Audit-write failures are isolated from already-executed trades so a logging problem cannot cause an order to be retried.


## v0.16.1 linked portfolio coordinator

The dashboard/monitor and autonomous loop now operate on one combined Base + Coinbase Advanced bankroll.
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
