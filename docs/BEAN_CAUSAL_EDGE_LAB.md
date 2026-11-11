# BEAN causal opportunity research lane

This is a NEW, independent **research-only** policy, not a modification of the five
October 23 frozen prospective strategies. It cannot trade, merge itself, claim
qualified forward profits, or promote any live policy. It uses the existing
public Coinbase six-hour candle reader and does not import wallet credentials.

## What changes

The earlier strategy sweep found attractive historical winners, but BEAN still
needed evidence for a more basic decision: **is this particular opportunity
likely to exceed both sides of the trading costs, or should we stay in cash?**

`src.bean_edge_lab` implements an explicitly fixed experimental rule:

1. Classify the completed candle using only prior four-bar and 16-bar price
   changes, plus 12-bar volatility (six possible trend/volatility contexts).
2. Use a fixed eight-bar holding horizon. Historical hypothetical fills occur
   on the second subsequent six-hour open, never the opening that coincides
   with receiving a completed candle. The hypothetical exit is eight bars later.
3. Learn from matching, already-matured contextual examples in a 240-bar
   trailing window, sampled with non-overlapping **holding horizons**. The
   features may be correlated, so this is not a claim of independent samples.
4. Estimate the fee-net return distribution and require at least six matched
   examples and a one-sided normal-approximation lower confidence bound above
   +50 basis points. The confidence approximation is a screening heuristic,
   **not a calibrated forecast guarantee**.
5. If the data do not support that threshold, stay in cash. Record exactly
   why: insufficient matured examples or insufficient fee-net edge.
6. Report hypothetical delayed-open orders, completed roundtrips, liquidation
   drawdown, and cash and fee-net passive baselines. Do not confuse a better
   return than cash with a better return than the passive benchmark.

Default modeled friction is 90 basis points taker fee per side plus half of a
10-basis-point roundtrip spread per side: **95 basis points each way**. This
requires about **1.93% gross roundtrip gain** to break even, before any
additional slippage or adverse selection. The 50-basis-point lower-bound buffer
is an extra conservative candidate rule, not a proven optimum.

## Public historical development command (PowerShell)

From the AIscend Python repository root:

```powershell
python -m unittest tests.test_bean_edge_lab -v
python -m src.bean_edge_lab --product BTC-USD --days 90 --output research/bean-edge-btc-development.json
```

The output path must be new; existing reports are not overwritten. Test any
other supported Coinbase USD/USDC pair by changing the product and output.
Failed or unavailable histories must remain visible as failures, not be
silently patched by manufacturing missing candles.

## Evidence limitations and next qualification

- **No independent market performance is claimed by unit tests.** Synthetic
  rising and flat candles verify mechanics, not an economically useful signal.
- Results from 90-day historical windows are reused development evidence,
  not fresh prospective or previously sealed holdouts.
- The 65% chronological boundary is an online causal diagnostic, not a
  sealed out-of-sample evaluation. It learns only after prior labels mature.
- A fixed regime, eight-bar hold, confidence threshold and fee assumptions
  are initial hypotheses, not winning fitted parameters.
- Next is **separate** cohort-and-time validation with true later candles,
  venue-specific observable spreads, quote depth, delayed fills, and explicit
  baselines. Candidate changes need new frozen identity and fresh prospective
  evidence.
- Never feed any outcome from this research branch into the original five
  October 23 frozen strategies or their running ledger. No live capital or
  automatic strategy adoption.

The purpose is to build a measured opportunity/abstention mechanism while
the original frozen collector continues unchanged.
