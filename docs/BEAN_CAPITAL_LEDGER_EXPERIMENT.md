# BEAN $1,000 paired capital ledger experiment

## What actually runs

Python module: scripts/bean_capital_experiment.py
Test module: tests/test_bean_capital_experiment.py
Workflow: .github/workflows/bean-capital-ledger.yml

This is a credential-free RESEARCH experiment, not a live trading controller.
The existing archived 389-symbol 500-iteration study (median ending $327.53)
is kept as a separate baseline reference. The new synthetic market generator
and one-symbol policies are different; do NOT pretend the two medians are a
paired apples-to-apples comparison.

Roles and grounded implementations:
1. Q learner: actual src.bean_growth_adaptive.BeanAdaptiveGrowthGate._apply_bar
2. Baseline grader: src.primary_strategy and src.bean_rotation_economics
3. Ledger independent checker: SHA-256 event replay + paired bankroll accounting
4. Optional BEAN Core: src.bean_core_bridge, separately checked-out real Core,
   revision-identified, read-only proposal on aggregate experiment evidence

Research design:
- 24 training seeds x 4 synthetic regimes = 96 training episodes with real
  Q reward updates. Train only, on synthetic market paths.
- Freeze Q weights/values BEFORE evaluation. Holdout does not mutate them.
- 125 previously unseen seeds x 4 regimes = 500 paired holdout market paths.
- Four different strategies see each same path: frozen BEAN Q learner,
  conventional price-action, buy-and-hold, and all-cash.
- Each evaluation starts with a fresh $1,000; 2,000 evaluations in total.
- 60 bps modeled taker fee + 5 bps half-spread + 5 bps slippage per side.
  The training reward model is the existing BEAN 60-bps approximation; the
  external independent evaluation charges 70 bps per side to all variants.
- Orders are next-open simulated fills only; no real Coinbase trade API.
- Hash-chained SQLite events include every model decision, simulated fill,
  terminal bankroll, friction, and drawdown. Every outcome is retained,
  including cash and losses; existing databases are never overwritten.
- The Core reviewer is advisory only and cannot authorize trading or set Q.
  No engineering-agent source rewriting is claimed.

From repository root in PowerShell, run:
    python -m unittest discover -s tests -p test_bean_capital_experiment.py -v
    python -m scripts.bean_capital_experiment --capital 1000 --train-seeds 24 --holdout-seeds 125 --periods 300 --db artifacts/bean-capital-evidence.sqlite3 --output artifacts/bean-capital-report.json

To use a locally checked-out Core, append:
    --bean-core ../BEAN

To independently replay the event hash chains:
    python -c "from scripts.bean_capital_experiment import verify_ledger; print(verify_ledger('artifacts/bean-capital-evidence.sqlite3'))"

The workflow produces a JSON scorecard, a complete SQLite ledger and optional
Core Bridge review. Promotion to live trading is always false.

Limitations: synthetic prices only, single-product comparison, no verified
historical or independently observed forward Coinbase profitability, no live
account proof, no guarantee of profit or readiness for deployment. A matching
hash chain is tamper-evidence, not a signed source-authenticity proof.
