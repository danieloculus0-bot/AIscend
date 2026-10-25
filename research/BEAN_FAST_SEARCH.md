# Faster BEAN research with a compact score ledger

Use the frozen Coinbase snapshot and the saved per-market teaching corpus again. Do not download candles or rebuild market features for every seed or parameter setting. The optimized environment removes redundant counterfactual simulation, replaces repeated full-window scans with a prefix index over immutable candles, and clones only the mutable account fields. It retains the exact original teaching rows, action decisions and detailed evidence.

The local synthetic mechanics benchmark measured approximately 14x faster repeated teaching generation. This is not an end-to-end training speedup or market-performance result. The GitHub benchmark also requires identical complete Core optimizer, replay, target, RNG and weight states compared with source `e49bb7e1052181a80be06ed23fd26316deae3ceb`.

## Compact active memory

`CompactScores` stores one trial outcome as eight reversible character pairs: method, dataset, window, seed, cost, configuration, evidence and reason. A 16-character mnemonic references a versioned dictionary; it does not round dollars, returns or drawdown. Exact scores and upstream file hashes/JSON pointers remain in SQLite and the existing append-only `DecisionLedger`. The index rebuilds from those original events. Each page supports 3,844 dictionary entries per field; overflow raises instead of recycling a code.

| Code | Meaning |
| --- | --- |
| P0 | Profitable and improves on matched control |
| P1 | Profitable but trails matched control |
| L0 | Losing but improves on matched control |
| L1 | Losing and trails matched control |
| T0 | Exact matched tie |
| D0 | Incomplete or unmatched evidence |

The simple signed score is incremental net return in basis points. Net profit, drawdown and reason remain separate fields, so a smaller loss is never presented as profitability. Comparisons require the same window, capital and asset set. Duplicate receipts cannot create new credit. Evaluation rows are retained, including failures, but never enter `rank()`. The normal query also checks actual recording time; explicit counterfactual historical research cannot see outcomes before their maturity.

Index the broad experiment once it completes:

```bash
python -m src.bean_compact_scores --results research/bean_wide_multistep_capital_20261024/results.json --output research/bean_compact_score_20261024
```

The command creates the pair legend, compact scoreboard, exact SQLite index and authoritative event stream. The original trade ledgers and full learned states stay in the source experiment. Faster indexing does not turn already inspected evaluation periods into new learning evidence.

Verify the optimization locally against the pinned reference checkout:

```bash
python -m scripts.bean_fast_benchmark --reference-root /path/to/e49bb7e-checkout --core-dir /path/to/pinned-BEAN --output benchmark.json
```
