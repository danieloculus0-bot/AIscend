# Architecture

The application is split into seven concerns:

- **Decision engine**: chooses BUY, SELL, or HOLD and a bankroll fraction.
- **Risk governor**: validates that an action stays inside the experiment account.
- **Execution/state engine**: executes the action and persists cash, positions, decisions, and ledger entries.
- **Wallet / venue adapters**: connect isolated capital to external systems without exposing unrelated accounts.
- **Windows UI**: starts or stops autonomy and displays state. It does not approve individual trades.
- **Browser UI**: exposes the same live engine through Flask for GitHub Codespaces.
- **Game scorer**: awards exactly one point per completed bankroll doubling and records speed separately.

The desktop application still defaults to the persistent synthetic market.

Version 0.3 adds a second execution path: a Coinbase Developer Platform non-custodial API-key **smart account** on Base / Base Sepolia, with WETH/USDC pricing, Permit2 allowance management, swaps, confirmation, and balance reconciliation.

## Capital path

Current sandbox:

```text
decision engine
    |
risk governor
    |
synthetic execution
    |
SQLite ledger
```

Wallet bootstrap:

```text
Windows Credential Manager
    |
CDP API credentials + wallet secret
    |
owner EOA
    |
named non-custodial smart account
    |
Base Sepolia / Base
```

Live architecture:

```text
market data
    |
decision engine
    |
risk governor
    |
live execution adapter
    |
CDP wallet
    |
Base
    |
onchain reconciliation
    |
immutable local ledger
```

## Credential boundary

CDP credentials are not stored in:

- Git
- SQLite
- the source tree
- the README
- command-line arguments

The setup utility prompts locally and stores the values through Python `keyring`, which uses the operating system credential backend on Windows.

The application loads those values into process memory only when the CDP adapter is used.

## Invariants

1. Cash never becomes negative.
2. No short selling in the current engine.
3. No leverage in the current engine.
4. An autonomous cycle never asks for per-trade approval.
5. Every decision is journaled, including rejected and HOLD decisions.
6. Closing and reopening the application preserves experiment state.
7. Product naming is not coupled to the core.
8. External wallet credentials never belong in the repository.
9. Wallet custody and trading execution remain separate components.
10. Live execution uses the named AIscend smart account and reconciles state from onchain balances after each swap.


## Browser / Codespaces path

```text
private GitHub repository
    |
GitHub Codespace
    |
Flask browser dashboard
    |
LiveEngine
    |
CDP smart account
    |
Base
```

The Codespace accepts the downloaded CDP key JSON and Wallet Secret into the running process, creates or retrieves the same named smart account, and can run one autonomous cycle or a timed loop.

## Game objective

```text
score = floor(log2(net liquidation / starting bankroll))
```

Each completed bankroll doubling is one point, while elapsed time is tracked separately for milestone speed. **Level 1 has a hard success condition: $100,000 net liquidation value.** The game is not considered won at Level 1 until that threshold is reached. Later level targets are intentionally undefined for now.


## Trader pet

The browser dashboard contains a deliberately crude pixel trader called `AIS-0`. It is cosmetic only and never controls execution. Its tiny face changes state for BUY, SELL, profit, loss, point milestones, idle, running, and errors, with minimal blinking and one-pixel movement.


## Multi-market opportunity layer

Version 0.9 adds a venue-capability registry and normalized opportunity scanner.

```text
public / venue market data
    |
OpportunityUniverse
    |
normalized opportunities
    |
Market Arsenal UI
    |
future decision allocator
    |
venue-specific executor
```

The four first-launch lanes are Crypto, Predictions, Stocks & ETFs, and Options. Research readiness and execution readiness are tracked separately per lane. Crypto currently has a live execution adapter. Prediction-market discovery uses public Kalshi market data. Stocks/ETFs and options remain visible and research-ready while their autonomous execution adapters are intentionally unconnected.


## Human Weather research layer

Version 0.10 adds a human-behavior signal plane.

```text
Coinbase price / volume / spread
        |
        +--------------------+
                             |
GDELT news + politics ------>|
Reddit social attention ---->| Human Weather fusion
Fear & Greed --------------->|
weird-event pressure -------->|
                             |
                    context-aware FOMO
                             |
                    composite research score
                             |
                       ResearchDecider
```

The final direction is a weighted blend of technical and human scores. Event intensity dynamically increases the human-information weight. Crowd heat is interpreted by regime rather than as a one-way signal: momentum-confirmed FOMO can be continuation fuel, euphoric reversal can indicate exhaustion, and extreme fear plus positive reversal can create a contrarian edge. External human feeds are fetched in parallel and cached to keep live cycles responsive.


## BEAN epistemic learning

Version 0.11 adds persistent epistemic memory beside the trading ledger.

BEAN stores typed claims, contradictions, predictions, outcomes, and source trust. Predictions are graded after their horizon expires. Signal trust is updated by realized direction and magnitude, with shrinkage toward neutral until enough samples exist.

```text
evidence -> typed claims -> contradictions -> predictions
                                      |
future price -------------------------+
                                      |
                                  grading
                                      |
                               learned trust
```

The first horizons are 5 minutes, 1 hour, and 6 hours for ETH, plus 1-hour predictions for strong candidates found by the rotating Coinbase asset-universe scanner.

## Coinbase research universe

The public research adapter discovers active USD/USDC Coinbase products and rotates through a bounded batch each trading cadence. Results are cached so browser refreshes do not create new market scans. BEAN records high-signal universe observations and eventually grades their predictions when the relevant asset is observed again.

Execution remains a separate capability. Research breadth does not imply the Base smart wallet can execute every Coinbase product.


## Dual live execution rails

Version 0.13 introduces a second live spot rail:

```text
research + BEAN
      |
      +--> Base smart wallet: USDC/WETH onchain
      |
      +--> Coinbase Advanced: account-eligible USD/USDC spot products
```

The rails keep independent state databases and credentials. The Advanced rail is intended for a dedicated Coinbase Advanced portfolio/API key so autonomous trading remains isolated from unrelated holdings. Market-lane enablement is broader than execution readiness: predictions, stocks/ETFs, futures, perpetuals, and options are enabled as strategy lanes even when their authenticated execution connectors are not yet wired.


## Linked live-capital invariant

Base and Coinbase Advanced are execution adapters for one AIscend experiment, not independent bankrolls.

```text
AIS-0 / Research / shared BEAN
            |
            v
     experiment bankroll
       start = $25
            |
      +-----+------+
      |            |
      v            v
 Base smart     Coinbase
  wallet        Advanced
      |            |
      +-----+------+
            |
            v
 combined decision/audit history
```

Moving USDC between the two rails is an internal transfer. It must not reset the game baseline, restart the game clock, or be interpreted as trading P/L. The Base live database remains the canonical BEAN/game-memory source for the current experiment; Advanced keeps rail-specific execution state while reading/writing the shared BEAN memory.

The browser runner has exactly one active execution rail at a time. Bridge completion hands the runner to the destination rail rather than leaving an empty source rail running.
