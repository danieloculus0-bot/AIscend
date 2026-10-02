# Architecture

The application is split into five concerns:

- **Decision engine**: chooses BUY, SELL, or HOLD and a bankroll fraction.
- **Risk governor**: validates that an action stays inside the experiment account.
- **Execution/state engine**: executes the action and persists cash, positions, decisions, and ledger entries.
- **Wallet / venue adapters**: connect isolated capital to external systems without exposing unrelated accounts.
- **Windows UI**: starts or stops autonomy and displays state. It does not approve individual trades.

The current autonomous execution venue remains a persistent synthetic market.

Version 0.2 adds the first real wallet infrastructure: a Coinbase Developer Platform non-custodial API-key wallet adapter using an EVM account on Base / Base Sepolia. Wallet identity and custody are deliberately separate from the future trading execution adapter.

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
named non-custodial EVM wallet
    |
Base Sepolia / Base
```

Target live architecture:

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
10. Mainnet execution must use the experiment wallet only, never an unrelated funding account.
