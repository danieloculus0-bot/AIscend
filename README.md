# Autonomous Capital Lab

Working-codename repository for a Windows autonomous-capital experiment.

**Premise:** give an autonomous agent a tiny bankroll, currently $10, and score it by what the bankroll becomes. Once autonomy starts, trades execute without per-trade human approval.

## Current milestone

The first milestone is a runnable Windows desktop sandbox that proves the complete loop:

1. Start with a persistent $10 bankroll.
2. Observe a market.
3. Let an autonomous decision engine choose BUY, SELL, or HOLD.
4. Validate the action against the account boundary.
5. Execute it automatically.
6. Persist positions, cash, decisions, and ledger entries to SQLite.
7. Revalue the portfolio and repeat.

The included synthetic market makes the application usable without an account, API key, brokerage, or internet connection.

A live-money execution venue is not wired in yet. The core is separated so a real provider can be added without rewriting the UI, ledger, bankroll logic, or decision loop.

## Windows quick start

Requires Python 3.11 or newer.

```powershell
git clone https://github.com/danieloculus0-bot/AIscend.git
cd AIscend

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The experiment database is stored under:

```text
%LOCALAPPDATA%\AutonomousCapitalLab\state.db
```

Closing the application does not reset the experiment.

## Build the EXE

From PowerShell:

```powershell
.\build.ps1
```

Output:

```text
dist\AutonomousCapitalLab.exe
```

GitHub Actions also tests the engine and builds the Windows executable automatically.

## Decision engines

### Built-in autonomous engine

Runs locally with no API key. It is intentionally aggressive and can deploy up to the entire bankroll.

### OpenAI-compatible AI

Choose **OpenAI-compatible AI** in the app and provide an endpoint and model. The endpoint must accept the common `/v1/chat/completions` request format.

If the endpoint requires a bearer token, set it before launching:

```powershell
$env:AUTOCAPITAL_API_KEY="your-key"
python main.py
```

The API key is never saved in SQLite.

## Account boundary

- No borrowing or leverage.
- Cash cannot become negative.
- An asset cannot be sold unless the agent owns it.
- The agent may deploy 100% of available bankroll.
- No per-trade confirmation once autonomy is running.
- Every decision and execution is journaled.

The governor sits below the decision engine so the model cannot grant itself access to money outside the experiment.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Naming

The product name is intentionally temporary. Set `AUTOCAPITAL_APP_NAME` to test another display name without changing the codebase.
