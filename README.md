# Autonomous Capital Lab

> Working codename. The product name is intentionally decoupled from the codebase.

A Windows desktop experiment built around one question:

**What can an autonomous AI turn a tiny bankroll into if it is allowed to make its own buy, sell, and hold decisions?**

The live experiment is intended to start with a tiny bankroll, currently around **$25**.

## Project status

**Version:** 0.17.0  
**Platform:** Windows x64 + browser via GitHub Codespaces  
**State:** Synthetic desktop sandbox + Coinbase live desktop mode  
**Live-money execution:** Base smart-account swaps and Coinbase Advanced spot orders are implemented

The current application is fully runnable against a persistent synthetic market. It already exercises the complete autonomous loop without requiring a brokerage account, wallet, API key, or internet connection.

Once autonomy is started, the agent does not ask for per-trade human approval.

## What works now

- Native-feeling Windows desktop UI built with PySide6
- Tiny bankroll experiment with live starting value read from the wallet
- Autonomous BUY, SELL, and HOLD decisions
- Built-in aggressive decision engine
- Optional OpenAI-compatible AI decision engine
- Persistent SQLite cash, position, ledger, and decision history
- Synthetic market with continuously changing prices
- Net liquidation value, P/L, cash, positions, and decision journal
- No leverage
- No negative cash
- No short selling
- Full-bankroll deployment is permitted
- Regression tests
- Single-file Windows executable build
- Versioned ZIP package
- SHA-256 package checksum
- GitHub Actions Windows build pipeline
- Coinbase CDP non-custodial wallet adapter
- Windows Credential Manager storage for CDP secrets
- Named EVM smart-account creation/retrieval
- Base and Base Sepolia balance inspection
- Base Sepolia test-fund bootstrap
- WETH/USDC live pricing through CDP
- Automatic Permit2 approval when required
- Autonomous USDC/WETH swaps with onchain reconciliation
- Separate live SQLite journal and decision history
- Coinbase setup directly in the Windows UI
- Execution selector for Synthetic, Base Sepolia, or Base
- Browser dashboard for real CDP execution
- GitHub Codespaces environment that auto-starts the browser app
- Game score with exactly one point per completed bankroll doubling
- Deliberately shitty dot-matrix trader pet whose face barely reacts to what the bot is doing
- Mandatory **Level 1** win condition at **$100,000 net liquidation value**
- First-launch **Market Arsenal** exposing Crypto, Predictions, Stocks & ETFs, and Options
- Public prediction-market discovery through Kalshi market-data APIs
- Cross-market opportunity normalization so future venue executors can plug into the same research layer
- **Human Weather** research layer combining global news, politics, weird-event pressure, social-media sentiment, Fear & Greed, and price action
- Context-aware **FOMO Index** where crowd heat can represent continuation fuel, exhaustion, or contrarian opportunity depending on confirmation
- Parallel external-signal collection with caching so the 60-second trading loop is not blocked by slow feeds
- **BEAN epistemic memory** for observations, inferences, hypotheses, predictions, contradictions, outcome grading, and learned source trust
- Rotating **Coinbase-wide USD/USDC research universe** so the research layer is no longer limited to ETH/BTC
- Multi-horizon prediction grading at 5m, 1h, and 6h for BEAN trust learning

## The loop

```text
market state
    |
    v
decision engine
    |
    v
risk governor
    |
    v
automatic execution
    |
    v
cash + positions + ledger
    |
    v
net liquidation value
    |
    +------ repeat ------+
```

## Game rules

AIscend is scored like a game whose objective is **maximum wealth growth in minimum time**.

```text
score = floor(log2(current bankroll / starting bankroll))
```

That means:

- Every **completed bankroll doubling is worth exactly 1 point**.
- 1x to less than 2x = 0 points, 2x to less than 4x = 1 point, 4x to less than 8x = 2 points, and so on.
- Time does not subtract points. Speed is tracked separately as time-to-milestone and acts as the tiebreaker.
- Trading more often does not itself earn points. Only net wealth and speed matter.
- HOLD is valid when waiting has better expected value than churn.
- No additional capital is added after the run starts.
- Point milestones are pure doublings: 2x, 4x, 8x, 16x, 32x, and so on.
- **Level 1 is not won until the bankroll reaches $100,000 net liquidation value.**
- $100,000 is the first level success measure, not the end of the overall game.

## Run it in a browser with GitHub Codespaces

No local install is required.

1. Open the private AIscend repository on GitHub.
2. Choose **Code > Codespaces > Create codespace on main**.
3. The dev container installs dependencies and starts `web_app.py` automatically.
4. Open the forwarded **AIscend Web** port when GitHub presents it.
5. In the browser dashboard, upload the downloaded Coinbase CDP API-key JSON and enter the Wallet Secret.
6. Select **Base · live money** and run one cycle or start continuous autonomy.

The browser dashboard shows the live wallet, bankroll, P/L, wealth multiple, game score, research brain, Market Arsenal, cross-market opportunity scan, positions, decision journal, and the intentionally terrible dot-matrix trader pet.

## Windows quick start

Requires Python 3.11 or newer. Python 3.12 is used by CI.

```powershell
git clone https://github.com/danieloculus0-bot/AIscend.git
cd AIscend

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The experiment database lives at:

```text
%LOCALAPPDATA%\AutonomousCapitalLab\state.db
```

Closing the application preserves the bankroll, positions, market state, ledger, and decision history.

## Build the Windows application

The repository has one canonical build path:

```powershell
.\build.ps1
```

The build script:

1. Reads the version from `VERSION`
2. Installs dependencies
3. Runs the regression suite
4. Compiles Python sources as a syntax sanity check
5. Builds a single-file Windows executable with PyInstaller
6. Creates a versioned Windows ZIP package
7. Generates a SHA-256 checksum

Expected output:

```text
dist\AutonomousCapitalLab.exe
dist\AutonomousCapitalLab-v0.1.0-windows-x64.zip
dist\AutonomousCapitalLab-v0.1.0-windows-x64.zip.sha256
```

For an already-prepared build environment:

```powershell
.\build.ps1 -SkipInstall
```

Tests can technically be skipped with `-SkipTests`, but normal local and CI builds run them.

## GitHub Actions

Every push to `main`, pull request to `main`, version tag matching `v*`, or manual workflow run executes the Windows pipeline.

CI:

- Uses `windows-latest`
- Uses Python 3.12
- Calls the same `build.ps1` used locally
- Requires tests to pass
- Requires PyInstaller packaging to succeed
- Re-verifies the generated SHA-256 checksum
- Uploads the EXE, ZIP, and checksum as a GitHub Actions artifact

That keeps local builds and CI from quietly becoming two different systems.

## Coinbase CDP wallet setup

Version 0.3 connects the decision/governor loop to a real Coinbase CDP capital rail.

The wallet is a **non-custodial CDP API-key smart account**. AIscend creates an owner account plus the named smart account `autonomous-capital`, reads balances on Base/Base Sepolia, prices WETH against USDC, and can execute swaps.

### 1. Create the CDP project

Sign in to the Coinbase Developer Platform portal with the dedicated project identity.

Create:

- A **Secret API key**
- A **Wallet Secret**

Do **not** paste either secret into ChatGPT, an issue, a commit, or a README.

### 2. Store the credentials locally

From PowerShell in the repository, you can load the downloaded CDP key JSON directly:

```powershell
.\configure-cdp.ps1 -KeyFile "C:\path\to\cdp_api_key.json"
```

The script reads the key ID/private key from the JSON and prompts for the Wallet Secret. Running `.\configure-cdp.ps1` without `-KeyFile` prompts for all three values.

The secret fields are not echoed.

They are stored through the Windows credential backend under:

```text
AutonomousCapitalLab/CDP
```

They are not written to the repository or SQLite database.

The setup then creates or retrieves:

```text
owner: autonomous-capital-owner
smart account: autonomous-capital
```

### 3. Check the wallet

```powershell
.\.venv\Scripts\python.exe scripts\cdp_wallet.py status
```

Default network:

```text
base-sepolia
```

Check Base mainnet without executing anything:

```powershell
.\.venv\Scripts\python.exe scripts\cdp_wallet.py --network base status
```

### 4. Test with fake money first

Request testnet USDC:

```powershell
.\.venv\Scripts\python.exe scripts\cdp_wallet.py faucet --token usdc
```

Or Base Sepolia ETH:

```powershell
.\.venv\Scripts\python.exe scripts\cdp_wallet.py faucet --token eth
```

Faucet calls are hard-blocked unless the selected network is `base-sepolia`.

### Remove the stored CDP credentials

```powershell
.\.venv\Scripts\python.exe scripts\cdp_wallet.py clear
```

### Live execution

The Windows UI now supports the full live path without requiring terminal setup.

1. Launch the application.
2. Click **CONFIGURE COINBASE**.
3. Select the downloaded CDP API key JSON.
4. Enter the Wallet Secret.
5. Choose **Coinbase live · Base Sepolia** or **Coinbase live · Base** under Execution.
6. Start autonomy.

The app stores the CDP credentials through Windows Credential Manager and creates/retrieves the dedicated AIscend smart account.

The command-line runner remains available for diagnostics and automation.

Inspect the live/testnet portfolio without trading:

```powershell
.\.venv\Scripts\python.exe scripts\aiscend_live.py --network base-sepolia status
```

Run one autonomous decision cycle:

```powershell
.\.venv\Scripts\python.exe scripts\aiscend_live.py --network base once
```

Run continuously:

```powershell
.\.venv\Scripts\python.exe scripts\aiscend_live.py --network base run --interval 60
```

The live runner uses the same decision engine and risk governor as the synthetic application. USDC is treated as cash and WETH as the first live risk asset. Decisions and executions are recorded in a separate `live-base.db` journal.

## Human Weather algorithm

Version 0.10 adds a second evidence plane beside technical market data.

The live score now blends Coinbase ETH/BTC price action with:
- GDELT global news and political/event headlines
- Reddit attention and sentiment from crypto and market communities
- Alternative.me Fear & Greed data
- crowd excitement / panic language
- politics and geopolitical risk pressure
- unusual-event / weird-news pressure
- momentum and volume confirmation

FOMO is intentionally contextual rather than treated as an automatic sell signal. Confirmed high-FOMO momentum can lower the entry threshold, euphoric reversal raises the threshold and can accelerate exits, and extreme fear plus a real price reversal can become a contrarian buy setup.

Human signals are cached between cycles to limit rate pressure while technical market data remains live.

## Decision engines

### Built-in autonomous engine

Runs locally with no model server and no API key.

It is intentionally aggressive because the experiment is starting with tiny capital. It can deploy up to 100% of currently available cash.

### OpenAI-compatible AI

Select **OpenAI-compatible AI** in the app and supply an endpoint and model.

The endpoint must support the common:

```text
/v1/chat/completions
```

request format.

If the endpoint requires a bearer token:

```powershell
$env:AUTOCAPITAL_API_KEY="your-key"
python main.py
```

The API key is read from the Windows environment and is not written to the experiment database.

## Account boundary

The agent currently operates under a deliberately small set of hard invariants:

- It cannot borrow.
- It cannot create negative cash.
- It cannot sell an asset it does not own.
- It cannot short.
- It can deploy 100% of its existing bankroll.
- It does not require human confirmation for each trade.
- Every decision is journaled, including HOLD and rejected actions.

The governor sits below the AI decision layer. A future model or strategy can change its behavior without gaining the ability to rewrite those account boundaries.

## Synthetic versus live execution

Synthetic desktop path:

```text
AI / strategy -> governor -> synthetic market
```

Live path:

```text
AI / strategy -> governor -> CDP smart-account adapter -> Base -> onchain reconciliation
```

The venue remains separate from the decision layer, so additional assets and execution venues can be added without rewriting the strategy/governor core.

## Tests

Run directly:

```powershell
python -m unittest discover -s tests -v
```

Or run the full build, which includes the tests:

```powershell
.\build.ps1
```

## Versioning

The application version lives in:

```text
VERSION
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

- `Linked portfolio - auto choose rail` is the default execution mode.
- Net liquidation, P/L, wealth multiple, Level 1 progress, and game points use the sum of both live rails.
- Capital can be split across Base and Advanced without AIS-0 interpreting the other rail as a loss.
- Each autonomous cycle evaluates both live adapters and selects one funded rail to execute/journal for that cycle.
- Exit signals are prioritized before new entries; otherwise the strongest executable funded setup wins.
- Manual bridge controls remain explicit. The coordinator does not silently move money between rails.
- Trade cycles and bridge transfers share one capital-operation lock so a transfer cannot race a live order.
- Counterpart rail snapshots are cached briefly for dashboard refreshes to avoid hammering provider APIs.
- Human Weather is fused into the Advanced candidate score while Advanced is active; the linked portfolio preserves that candidate-specific context instead of replacing it with a display-only Base copy.


## v0.16.1 repo audit

The linked-capital coordinator now refuses to select the Base execution adapter for BUY/SELL signals when Base has no executable cash/position. This closes the empty-rail failure mode exposed after moving the bankroll to Coinbase Advanced. The live runner also reports the actual rail selected on each linked-auto cycle so the dashboard shows whether AIS-0 is currently operating through Base or Advanced.


## v0.17 Coinbase-wide market arsenal

AIscend now treats Coinbase as a multi-market research surface rather than only a
crypto execution screen.

- Coinbase Advanced keeps live linked-rail crypto execution across every
  account-tradable spot quote currency.
- Human Weather is a real Advanced scoring input. The same cached public
  news/social/Fear & Greed/politics/weirdness observation used by Base is
  contextualized against each Advanced candidate's own momentum and volume.
- The prediction-market scanner walks the open underlying Kalshi market feed
  used by Coinbase predictions with cursor pagination instead of sampling only
  the first page.
- Prediction markets are classified into sports, politics, crypto, weather,
  economics, culture and other research buckets. Sports markets are also tagged
  by league/sport when identifiable.
- Prediction-market priority is explicitly a market-quality score based on
  liquidity, spread and time-to-close. It is not mislabeled as directional edge.
  Until an independent probability model exists, YES/NO prices remain
  market-implied probabilities and the edge state is UNMODELED.
- The Market Arsenal ranks crypto directional opportunities and prediction
  market-quality opportunities with the score type shown in the UI. While the
  live runner is active, the browser refreshes the arsenal roughly once per
  minute.
- Prediction-market autonomous execution remains disabled. Coinbase exposes the
  products through Coinbase Financial Markets, but AIscend will not pretend a
  Coinbase-native programmatic order path exists until a supported connector is
  available for the account.
- Stocks/ETFs, futures, perpetuals and options remain declared future lanes.
  Their capability text now says plainly that live discovery/execution adapters
  are not connected yet.
