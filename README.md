# Autonomous Capital Lab

> Working codename. The product name is intentionally decoupled from the codebase.

A Windows desktop experiment built around one question:

**What can an autonomous AI turn a tiny bankroll into if it is allowed to make its own buy, sell, and hold decisions?**

The live experiment is intended to start with a tiny bankroll, currently around **$25**.

## Project status

**Version:** 0.5.0  
**Platform:** Windows x64 + browser via GitHub Codespaces  
**State:** Synthetic desktop sandbox + Coinbase live desktop mode  
**Live-money execution:** CDP smart-account swaps on Base are implemented

The current application is fully runnable against a persistent synthetic market. It already exercises the complete autonomous loop without requiring a brokerage account, wallet, API key, or internet connection.

Once autonomy is started, the agent does not ask for per-trade human approval.

## What works now

- Native-feeling Windows desktop UI built with PySide6
- Persistent $10 bankroll
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
- Game score, milestone tracking, and configurable FORTY ACRES victory target

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
score = 1000 × log2(current bankroll / starting bankroll)
        - 10 × elapsed days
```

That means:

- Every bankroll doubling is worth about **+1000 points**.
- Time continuously costs points, so reaching the same bankroll sooner scores higher.
- Trading more often does not itself earn points. Only net wealth and speed matter.
- HOLD is valid when waiting has better expected value than churn.
- No additional capital is added after the run starts.
- Milestones are 2x, 5x, 10x, 25x, 100x, and 1000x.
- Final victory is **FORTY ACRES**: net liquidation reaches the configured dollar target for buying 40 acres up north.

The browser UI lets the target price be changed without changing code.

## Run it in a browser with GitHub Codespaces

No local install is required.

1. Open the private AIscend repository on GitHub.
2. Choose **Code > Codespaces > Create codespace on main**.
3. The dev container installs dependencies and starts `web_app.py` automatically.
4. Open the forwarded **AIscend Web** port when GitHub presents it.
5. In the browser dashboard, upload the downloaded Coinbase CDP API-key JSON and enter the Wallet Secret.
6. Select **Base · live money**, set the Forty Acres target, and run one cycle or start continuous autonomy.

The browser dashboard shows the live wallet, bankroll, P/L, wealth multiple, game score, positions, and decision journal.

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

## Next major milestone

Expand the live asset universe beyond the first WETH/USDC pair and add richer live-market intelligence to the autonomous decision engine.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the current component boundaries and invariants.
