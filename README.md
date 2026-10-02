# Autonomous Capital Lab

> Working codename. The product name is intentionally decoupled from the codebase.

A Windows desktop experiment built around one question:

**What can an autonomous AI turn a tiny bankroll into if it is allowed to make its own buy, sell, and hold decisions?**

The current seed bankroll is **$10**.

## Project status

**Version:** 0.1.0  
**Platform:** Windows x64  
**State:** Functional autonomous sandbox  
**Live-money execution:** Not connected yet

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

The score is intentionally simple:

```text
current net liquidation value / original bankroll
```

If the agent turns $10 into $6.14, its bankroll is now $6.14. There is no magic reset unless the experiment is manually reset.

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

Today:

```text
AI / strategy -> governor -> synthetic market
```

Target architecture:

```text
AI / strategy -> governor -> execution adapter -> real venue
```

The live execution adapter is intentionally not baked into the UI or decision engine. Brokerage, wallet, marketplace, or other venue integrations should plug into the execution layer without rewriting the rest of the application.

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

Replace the synthetic execution venue with the first real $10-capable account or marketplace adapter while preserving the existing autonomous decision loop, bankroll isolation, ledger, and governor.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the current component boundaries and invariants.
