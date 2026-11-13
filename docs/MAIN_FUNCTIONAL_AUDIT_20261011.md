# AIscend Main Functional Audit — 2026-10-11

**Baseline:** `main` commit `6d1c509fdb3df7dda97303e92adb9c9d4ac769ba`.  
**Audit work:** [PR #27](https://github.com/danieloculus0-bot/AIscend/pull/27).  
**Research branch:** [PR #24](https://github.com/danieloculus0-bot/AIscend/pull/24), separate and not merged.

This audit reviewed the full main repository inventory, entrypoints, test coverage, and major execution/data flows. It is **not** a claim of having executed every API path, inspected a live wallet, or verified profitable trading. The user’s live data, exchange permissions, and actual fills were unavailable to this audit.

## Functional inventory

| Area | Main implementation | Assessment |
|---|---|---|
| Simulation, local state, scoring | `src/core.py`, `src/game.py` | Exists; synthetic simulation and bankroll milestones separate from real account profitability |
| Base EVM wallet and swaps | `src/wallets/cdp_wallet.py`, `src/live.py` | Exchange/swaps integrated; USDC/WETH Base rail only; live fills not reproduced in audit |
| Coinbase Advanced | `src/wallets/advanced_trade.py`, `src/advanced_live.py` | Broad spot discovery/execution; fixed buy/sell signal thresholds; no cost-aware automatic sell-to-rotate |
| Coinbase discovery | `src/asset_universe.py` | All public online spot products, rotating batches; authenticated tradability filtered later |
| New listings | `src/listing_sentinel.py` | Tracks catalog changes and full-trading events; ranking boost not empirically proven profitable |
| Research, sentiment | `src/research.py`, `src/sentiment.py`, `src/opportunities.py` | Market and human factors; opportunity view contains markets not necessarily executable via current rails |
| BEAN integration | `src/bean.py` | Embedded Bayesian/epistemic observation and prediction memory; **not** the latest BEAN Core autonomously editing trade logic |
| Decisions, auto routing | `src/portfolio.py`, `web_app.py` | Rail coordination, journaling and worker loops; no complete portfolio opportunity-cost optimizer |
| Audit and tax records | `src/audit.py` | Durable append-only event records, not yet authoritative reconciliation of fills and fees |
| UI and remote access | `src/ui.py`, `templates/`, `web_app.py`, `scripts/start_remote_monitor.ps1` | Local dashboard and optional remote read-only monitor |
| Operations/build | `build.ps1`, `scripts/`, `.github/workflows/windows-build.yml`, `requirements.txt` | Windows build pipeline; regression suite available; deployment to user PC is separate |

## Verified code issues addressed in PR #27

1. **High: rejected Coinbase market orders could be journaled as executed.** Require explicit provider success in the execution wrapper and again at engine ingress. Preview errors now stop execution before order placement. This confirms API acceptance, **not final fill**.
2. **High: web server exposed trading controls on all interfaces by default.** Bind to loopback by default. Remote Tailscale launcher now requires a monitor token and binds only to the Tailnet IP. Remote requests may only read the authenticated monitor; trading and credential endpoints are local-only.
3. **Medium: Coinbase catalog could starve other assets when failed scans never entered cache.** Advance cold-start selection cursor even after failures. Reject invalid, crossed or nonfinite order books before ranking.
4. **Medium: raw scoring could override a real zero-valued/negative fused signal.** Honor the derived decision score for rail and live selection.
5. **Medium: Advanced portfolio undervalued open-order-reserved balances.** Account for available + on-hold balances in NAV, but use only available quantity for order sizing. Surface `valuation_complete` and `unpriced_balances`.

## Unresolved engineering findings

**Critical to future autonomous strategies:**

- Live AIscend only invokes its embedded `BeanMemory` observations; BEAN Core's latest reasoning / code-authoring pipeline is not the primary trade policy. PR #24's existing adapter is advisory and defaults to a mock reasoning adapter, not an independently proven live trading intelligence.
- Current execution generally sells only weak scored holdings; it does not systematically rotate out of stagnant / profitable positions into measurably better opportunities when quote cash is fully invested.
- Exchange create-order success is not a filled-trade receipt. Journal reconciliation should fetch provider order/fill status, actual quantities, partial fills, weighted execution price and charged fees, including uncertain-response idempotency.
- `valuation_complete=False` can occur for holdings without resolvable USD rates. Current partial estimated NAV must not be used as an unquestioned proof of bankroll growth or a Level 1 victory. Consider deferring scoring on incomplete valuation.
- API/product listing is not actual wallet trade authorization; minimum order size, quantity increments, quote denomination and dynamic tradability must be checked immediately before execution.
- Strategy school in PR #24 performs parameter enumeration, not independently model-authored new algorithms. Historical acceptance across overlapping 30- and 60-day ranges is **not** independent validation. Real forward paper tests and nonoverlapping held-out regimes are required before performance claims.
- No evidence of a profitable strategy was found in this audit. More brute-force retries over the same validation set would increase false discoveries.

## Release criteria

- Run `python -m unittest discover -s tests -v` and Python compilation on the PR code.
- Run Windows Build CI successfully on the final head SHA.
- Keep live trading execution unchanged until per-rail order state machine and order-fill reconciliation are independently verified.
- Merge PR #27 (main functional fixes) separately from PR #24 (strategy research), resolving shared file conflicts intentionally.
- Install updated Windows code only after confirmed merge, using the local environment update workflow; GitHub modifications do not update a running local Python process.

The purpose of PR #27 is to make the base platform more reliable. It does **not** assert that BEAN has discovered a profitable crypto strategy, nor that changes have reached the user's local installation.
