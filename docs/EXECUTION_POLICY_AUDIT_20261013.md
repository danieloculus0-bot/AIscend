# AIscend execution and policy audit — October 13, 2026

## Goal and responsibility

**AIscend is the crypto trading application.** It discovers Coinbase markets,
uses standard systematic signals, compares full net trading economics,
holds/exits positions, and submits orders. **BEAN is an independent research and
verification assistant**; its simulated policy or historical cash selection
must not veto or originate trades on either funded rail.

## Current trading scope

- Coinbase Advanced: spot market buys/sells, eligible assets and USD/USDC
  quote conversions when a supported direct pair exists; no shorts/leverage.
- Coinbase Base: existing native decider and funded WETH/USDC swap execution;
  the BEAN growth table is evidence only.
- Never treat configured wallet credentials or a browser page as proof a live
  order was placed; runner must be active and journal must record the cycle.
- One market-order leg per live cycle, followed by a newly synchronized balance
  snapshot before a dependent conversion or reinvestment. There is no atomic
  multi-leg route and execution may legitimately fail mid-route.
- No app-side 85% capital ceiling, arbitrary fixed 18-bps rotation veto, or
  historical BEAN cash veto. Valid account available balances, venue increments,
  minimum order sizes and Coinbase previews are required technical checks.

## Cash not moving: exact diagnostic categories

1. Runner stopped/incorrect execution rail; last runner cycle and timestamp.
2. Public scanner unavailable, incomplete, or no candidate strategy signal.
3. Candidate exists but full expected buy+sell+bridge+incumbent sale costs
   exceed *uncalibrated* estimated gross opportunity.
4. Quote route not available (e.g., USD position with USDC only and no
   USDC-USD venue pair); or current held asset signals better than the route.
5. Position still within estimated maturation window, no invalidation signal.
6. Insufficient spendable size or balance reserved by open exchange orders.
7. Coinbase buy/sell preview or order submission rejected, with error journaled.
8. Order accepted but actual fill/settlement is not yet independently verified.

Access local /api/trading-diagnostics for the current plan, skip counts,
last decision, runner timestamps/errors, and order attempt status. The dashboard
shows the primary-policy result separately from BEAN's research controls.

## Profit and evidence claims

- Live primary signal strategies: trend, breakout and reversal.
- The extrapolated gross-basis-point signal and maturity horizon are
  hypotheses, **not calibrated future returns or win probabilities**.
- src/strategy_research.py replays only observed history, fills hypothetically
  at the next bar open, deducts taker/spread costs, selects on training, and
  reports a separate untouched holdout and buy-and-hold comparison.
- Nightly GitHub public-data research reports are retained as artifacts;
  failure and inconclusive results are preserved rather than hidden.
- Future research can propose strategy improvements, but **no model auto-
  promotes itself into funded live execution from a single backtest**.
- Full reconciliation of actual Coinbase fills, fees, slippage and realized
  customer-account P/L is not yet independently verified. Order submission
  acceptance, mock tests, historical profits and actual settled gains are
  different evidentiary levels.
- No algorithmic profitability or Level 1 $100,000 outcome is guaranteed.

## Validation matrix

- Unit: strategy eligibility and spread-inclusive economics.
- Unit: no-lookahead training/holdout, fee-sensitive replay and insufficient
  history not reported as success.
- Mock exchange: sell legacy coin → USD/USDC conversion → buy candidate,
  with errors visible and no BEAN veto.
- Browser: valid JavaScript, status/diagnostic endpoints, runner status,
  and remote-monitor read-only protections.
- Platform: Python regression on Windows and Linux, Windows packaging checks.

At merge time, link the successful workflow runs and add the result of any real
fee-rate/preview and live-fill reconciliation before claiming production
profitability.
