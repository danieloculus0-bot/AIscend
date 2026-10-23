# BEAN prospective SQLite ledger — Python / PowerShell

The default manifest is `research/bean_frozen_prospective_20261023_fee90.json`.
It starts a new paper ledger at the published U.S. Coinbase Advanced entry-level
taker fee of 90 bps per side, plus the separately modeled 10 bps round-trip
spread. The user's personal fee tier and realized spread/slippage remain unknown;
reconcile actual fills before interpreting paper performance.

This is a **paper-only**, observational SQLite database. It does not submit Coinbase
orders, access private keys, approve promotion, or guarantee net profitability.

## Run directly from the Python source checkout

Activate the same Python environment you use for `python main.py`.
From the repository root:

```powershell
python -m src.bean_sixhour_forward --verify-only
```

The command **only reads** an existing database. If none exists, it fails
rather than creating a misleading empty success report.

To initialize the frozen strategies using real public Coinbase candles and
commit the first prospective observation:

```powershell
python -m src.bean_sixhour_forward
```

To continue in the same PowerShell session and preserve decisions across
process restarts:

```powershell
python -m src.bean_sixhour_forward --watch
```

The observer checks after newly completed six-hour candle boundaries, retries
temporarily unavailable public candle data, and writes only confirmed
chronological hypothetical observations. Ctrl+C stops it. It does **not**
continue running after you close the PowerShell process; an always-on
Python process or an explicitly configured local scheduled task is required.
No automated background execution has been proven merely from GitHub CI.

## Where records actually go

The default SQLite database is:

`%LOCALAPPDATA%\AutonomousCapitalLab\bean-sixhour-prospective.sqlite3`

It is **not** in `artifacts/`, the Git checkout, or temporary CI storage.
This location survives ordinary Git pulls and source checkout changes, but
it may be lost if Windows or the user profile is wiped during reinstall.

Each `--watch` observation with a newly committed ledger tip also makes a
verified snapshot under:

`%LOCALAPPDATA%\AutonomousCapitalLab\bean-ledger-backups\`

Each backup carries an SHA-256 sidecar. These are on the same computer by
default, so before reinstalling, copy a verified backup and its checksum to
another drive or another location that survives the reinstall.

## Verify or back up without exchange requests

```powershell
python -m src.bean_sixhour_forward --verify-only
python -m src.bean_sixhour_forward --backup-to "D:\BEAN-backups\paper-ledger.sqlite3"
```

`--backup-to` uses SQLite's online backup mechanism, not an unsafe raw copy
of the potentially active WAL database. It refuses to overwrite an existing
backup and verifies the copied event hash chain against the original before
reporting success. The SQLite state, manifest SHA, cumulative SHA-256 linked
ledger, positions and Q values are checked on every subsequent run.

For recovery on a new Windows profile, **stop all observers first**, preserve
the manifest unmodified, and restore a verified backup to the default SQLite
path. Re-run `--verify-only` before `--watch`. Never combine unrelated
databases, copy only a live `-wal` sidecar, overwrite the current journal
or reinitialize with an altered manifest.

## Distinctions that matter

- **Verified SQL history**: an openable SQLite database with a correct event
  sequence, SHA-256 parent linkage, persisted position hashes and frozen
  strategy identity. The local checksum sidecar provides an additional
  offline checkpoint for backups.
- **Forward paper evidence**: hypothetical entry at a previously committed
  future open, with modeled taker fee and spread; not a Coinbase venue fill.
- **Trading profit**: not proven by SQLite integrity, green tests or a
  profitable historical holdout. Real account P/L still requires authenticated
  venue fills, deposits/withdrawals and account-specific fee reconciliation.

If multiple Python invocations overlap, `BEGIN IMMEDIATE` serializes ledger
writers within a 30-second SQLite busy timeout. An observation either commits
the entire event/state update or rolls it back. A missing market bar blocks
speculative forward credit rather than inventing an execution.
