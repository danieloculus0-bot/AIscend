# BEAN Unattended AIscend Engineering — Local Windows

**Purpose:** Allow BEAN + local Ollama to diagnose, edit, test, repair, and adopt improvements to AIscend without requiring the owner to approve each code change or each research candidate.

## How it operates

1. Run `scripts/start_bean_engineer.ps1` from an AIscend Git checkout on Windows where Ollama and a chat/coding model are installed. Start once; the `--loop` runner repeats without permission prompts.
2. The local Ollama model receives source code and prior outcome receipts; it proposes structured literal edits to an engineering target. The model does **not** supply shell commands to execute.
3. A separate Git worktree receives the proposed patch. The host runs Python compilation and the complete regression suite. Failed code is not adopted. Each failed attempt is recorded for next time.
4. Passing changes become commits, fast-forward into local `main`, and the launcher rebuilds the Windows package and pushes `main` to GitHub automatically. No approval gate.
5. Experiment memory is recorded in `%LOCALAPPDATA%\AutonomousCapitalLab\bean-autonomous-engineer.jsonl`; Strategy School retains its own SQLite history. This file is **not automatically imported** into the separate BEAN Core `bean_memory.db` database.
6. The browser and desktop Strategy School interfaces no longer ask the owner to approve a qualifying research candidate. An experiment is marked `AUTO_SELECTED` when the historical test beats modeled cash and BTC; `REJECTED_BY_EVIDENCE` otherwise.

## Launch from an updated AIscend working tree

```powershell
.\scripts\start_bean_engineer.ps1
```

One iteration, for a dry inspection of the unattended workflow (a successful candidate is still adopted):

```powershell
.\scripts\start_bean_engineer.ps1 -Once
```

Choose a local Ollama model (must already be installed):

```powershell
.\scripts\start_bean_engineer.ps1 -Model 'qwen2.5-coder:1.5b'
```

Use Ctrl+C to stop the **local** unattended engineering loop. Starting the script is the one-time action that grants BEAN permission to perform successive iterations. No per-iteration reviews, checkboxes or confirmations.

## Honest scope and operational notes

- The autonomous code engineer is independent of the BEAN Core Ollama *reasoning-provider* branch while that is being finalized. It directly uses the local Ollama HTTP API and stores AIscend-specific experiment receipts. BEAN Core's own SQLite memory ingestion still needs integration.
- It writes to and tests the AIscend repository as the currently logged-in Windows user, not as a new security-isolated user. The $25 deposited for trading **does not** limit the operating-system permissions of Python, PowerShell, or model-authored code.
- The script never instructs Ollama to produce arbitrary shell commands; all commands are host-defined Git, Python and PowerShell operations. No admin/UAC escalation is part of this feature.
- New source changes affect subsequent Python launches; an already running executable is not hot-replaced. The rebuilt ZIP/executable in `dist/` is a new release artifact, not an automatic restart of a currently-running trader.
- Auto-selected Strategy School candidates are research outcomes, **not automatically attached to the live execution policy**. The autonomous engineer can edit the live policy source and test it, but the code runs in live mode only when that version is launched.
- The recorded 12-day single-market-cohort holdout can become contaminated if repeatedly examined. Even a positive recorded holdout is not independent live profitability proof. Trading fees, execution, and available funding may differ materially.
- BEAN's editing decisions remain constrained to the AIscend source-target set and a clean local `main` checkout. The local experiment does not need human approval, but failed tests do not become passing commits.
- Before permitting live trading after autonomous updates, ensure the connected wallet/account contains only the intentionally risked funds: the project's research bankroll constant is **not** a cryptographic spending cap on a multi-asset Coinbase account.
