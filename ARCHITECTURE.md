# Architecture

The application is split into four concerns:

- **Decision engine**: chooses BUY, SELL, or HOLD and a bankroll fraction.
- **Risk governor**: validates that an action stays inside the experiment account.
- **Execution/state engine**: executes the action and persists cash, positions, decisions, and ledger entries.
- **Windows UI**: starts or stops autonomy and displays state. It does not approve individual trades.

The current execution venue is a persistent synthetic market. A future live venue should implement the same concepts while leaving the decision and UI layers unchanged.

## Invariants

1. Cash never becomes negative.
2. No short selling.
3. No leverage.
4. An autonomous cycle never asks for per-trade approval.
5. Every decision is journaled, including rejected and HOLD decisions.
6. Closing and reopening the application preserves experiment state.
7. Product naming is not coupled to the core.
