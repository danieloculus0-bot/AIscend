# AIscend strategy research database

This collection contains 289 algorithm specifications in 20 families, 75 source records, 27 data-source contracts, a five-year-window market/on-chain snapshot, 3,184 daily Fear and Greed observations, and 105 captured publisher headlines. The source archive supplies 1,953,796 asset-day records for 2,235 assets. The finalized index has USD price observations for 1,603 assets, using `PriceUSD` and/or `ReferenceRateUSD` as explicitly named fields.

The universe includes inactive assets, short histories and some non-crypto instruments. It is not a claim that 2,235 cryptocurrencies trade on Coinbase. No observation is silently padded to five years. Every per-asset date range, source blob, missing-window exclusion and checksum is retained in [history_manifest.json](history_manifest.json).

## Open the collection

- [Complete algorithm catalog](CATALOG.md)
- [Machine-readable strategies](strategies.jsonl) and [source register](sources.jsonl)
- [Dataset access and rights](datasets.jsonl)
- [Compressed SQLite database](strategy_research.sqlite3.gz)
- [Snapshot counts and checksums](snapshot_receipt.json)
- [Frozen BEAN narrative context](BEAN_CONTEXT.json)
- [Collection search audit](collection_audit.json)

Download/clone this branch and run from the AIscend repository root with Python 3.11. These commands need only Python's standard library:

```bash
python -m scripts.strategy_atlas stats
python -m scripts.strategy_atlas query --family carry_arbitrage
python -m scripts.strategy_atlas query --text reversal --limit 40
python -m scripts.strategy_atlas history btc --metric PriceUSD --start 2023-01-01T00:00:00Z --limit 5
python -m scripts.strategy_atlas history sol --metric ReferenceRateUSD --limit 5
python -m scripts.strategy_atlas context --output artifacts/current_bean_context.json
```

`Atlas` opens the compressed SQLite snapshot through a temporary decompressed file. `history_chunks` stores one checksum-verified compressed observation stream per asset inside the database. `Atlas.history()` queries an asset, metric and date range without fabricating OHLC candles. `sources`, `strategies`, `strategy_sources`, `data_sources`, `documents` and `experiment_events` are ordinary indexed SQLite tables. Fear and Greed observations and the collection manifest are available through `Atlas.metadata()`.

## What is implemented

132 specifications have numeric snapshot entry/exit rules, including 61 signed candlestick indicator templates. The evaluator requires every named input and its availability timestamp, rejects future/nonfinite/missing inputs, and returns a research target direction. It does not calculate all indicators, size an order, fit a model, simulate a fill or approve trading. A TA-Lib pattern score must be computed by the separately installed reference library or an independently verified implementation.

The remaining 157 strategies have explicit steps, input contracts, sizing/cost/execution requirements and failure conditions. They require specialized portfolio, options, orderbook, DeFi or training engines. Their existence in a database does not mean those engines or trained weights have been implemented. Each row labels its implementation status. Thresholds are starting hypotheses, not optimized claims or exact reproductions of cited papers.

All discovered negative/mixed findings remain available. The catalog includes trend reversals, failed cost-adjusted technical rules, adverse-selection/LP losses, short-squeeze and carry failures, and capped martingale counterexamples. The retracted taxonomy reference is labeled and supplies no efficacy evidence. None of the collected strategies was promoted to live trading or shown profitable by this collection.

## Narrative communication with BEAN

The supplied pipeline captures four public RSS feeds and stores headline metadata, original publication time, actual first-seen time, availability time, canonical URL, content hash, publisher group, named asset evidence, themes and event types. The packet preserves positive, negative and uncertain evidence and identifies both-sided themes. Articles are evidence, never instructions to execute tools or alter strategy code.

`available_at = max(published_at, first_seen_at)`. An article published months ago but collected today cannot become an advance signal in yesterday's backtest. Exact normalized headline syndication is collapsed; paraphrases and common ownership are not comprehensively resolved. Raw counts are exposed, but attention z-scores remain unavailable until comparable prospective baseline windows exist. Current extraction is explicitly a headline lexicon, not a trained FinBERT/CryptoBERT or language-understanding model. The optional model-annotation JSON schema defines a later integration contract and does not pretend a model is installed.

```bash
python -m scripts.strategy_atlas collect-news --feeds https://cointelegraph.com/rss https://www.coindesk.com/arc/outboundfeeds/rss/ https://decrypt.co/feed https://cryptoslate.com/feed/ --output artifacts/news.jsonl
```

To maintain prospective history, decompress the published database into a separate writable working file, then ingest newly captured records and export a fresh packet:

```bash
python -c "import gzip,pathlib; pathlib.Path('artifacts/working.sqlite3').write_bytes(gzip.decompress(pathlib.Path('research/strategy_atlas/strategy_research.sqlite3.gz').read_bytes()))"
python -m scripts.strategy_atlas --db artifacts/working.sqlite3 ingest-news --input artifacts/news.jsonl
python -m scripts.strategy_atlas --db artifacts/working.sqlite3 context --output artifacts/bean_context.json
```

The existing `src.bean_core_bridge.observe_with_current_bean` accepts this packet as its `observation` argument. This collection does not assert that the existing Core adapter has been executed with a real language-model backend. The companion BEAN-AI-Bridge research adapter validates packet checksums and timestamps and persists it in a separate market-research ledger. The existing ERP KPI tables are not used for financial records.

## Experiment retention

Register a complete configuration, data checksum, chronological splits, costs, start/finish/observation times, result, metrics, evidence ids and a two-character reason code. `append_experiment` preserves POSITIVE, NEGATIVE, INCONCLUSIVE, DATA_UNAVAILABLE and ERROR outcomes equally in an append-only hash chain. Unknown causes use `UN`; losing trials are not automatically assigned an invented root cause. `recall_experiments(as_of=...)` only exposes results finished and observed before that decision time.

The database starts with no economic experiment results because this task collected research and data. Local software tests are not financial backtests. Proposed models must still undergo chronological fit/validation, overlapping-label purging, all-trials accounting, cost and liquidity stress tests, delisting handling, independent holdout and prospective paper evaluation. Do not rank hundreds of opened holdouts and call the hindsight winner a fresh discovery.

## Dataset rights and limitations

Coin Metrics archive data is attributed to Coin Metrics, Inc under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/); see [the copied provider license](COIN_METRICS_DATA_LICENSE.txt). This snapshot is for noncommercial research. Commercial AIscend deployment needs appropriate data rights. Current-vintage on-chain values and labels are not a point-in-time history. Reference prices are neither venue quotes nor executable fills.

Coinbase's [August 7, 2026 market-data terms](https://www.coinbase.com/legal/market_data) restrict AI/ML use and redistribution without written permission. This package does not redistribute Coinbase candles or use them to train BEAN. Other documented APIs have their own access/history/rights contracts; DOCUMENTED_NOT_COLLECTED is not the same as downloaded data.

The Fear and Greed series is attributed to [Alternative.me](https://alternative.me/crypto/fear-and-greed-index/), which permits commercial use with the required attribution next to displayed data. Its retrospective API does not supply original first-seen timestamps. Publisher records contain headlines and metadata only; full article bodies are not republished.

## Reproduce

```bash
python research/strategy_atlas/build_catalog.py
python research/strategy_atlas/build_datasets.py
python research/strategy_atlas/collect_history.py --revision f1a36afb962731c387bb03982758ab0103063da5 --cutoff 2026-10-24 --years 5
python research/strategy_atlas/package_snapshot.py
python -m unittest discover -s tests -p test_strategy_atlas.py -v
```

The one-time research-branch build workflow recreates and commits the database, provenance and frozen context from the pinned upstream archive. It also publishes a GitHub Actions artifact. There is no recurring schedule or funded execution.

This is a finite, broad taxonomy with traceable coverage, not a claim to have scraped all internet data or every possible algorithm. The source and dataset schemas allow additional strategies, contrary evidence, market history and narrative annotations to be added without discarding failed candidates.
