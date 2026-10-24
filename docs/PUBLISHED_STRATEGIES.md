# Published systematic strategies for AIscend Python desktop

Research-only source. No wallet, credentials, funded orders, or automatic trading promotion.

## Implemented published rule families

- Donchian System 1: close-confirmed 20-day previous high breakout; exit on prior 10-day low. System 2: 55-day previous high entry and 20-day low exit. Original source: Original Turtle Trading Rules, with spot close-confirmed execution adaptation.
- Banded moving averages: 10-day and 30-day simple moving averages, with 2% and 4% band variants and symmetric exits. Research: Svogun & Rudys (2024), https://doi.org/10.1080/13504851.2024.2394211. Our choices of parameters are variants, not an exact paper replication.
- Time-series momentum: 30-day or 60-day own prior return, enter after positive signal threshold, exit when return turns negative. Research: Moskowitz, Ooi & Pedersen (2012), https://doi.org/10.1016/j.jfineco.2011.11.003. Original futures/volatility scaling are NOT reproduced by this unlevered spot variant.
- On-balance volume: rolling signed volume combined with price trend. Technical rule family studied by Deprez & Froemmel (2024), https://doi.org/10.1016/j.iref.2024.05.003. Independently specified variant, not their exact strategy.

Run from the same Python source checkout and environment used for python main.py:

    python -m src.published_strategy_research --product BTC-USD --product ETH-USD --product SOL-USD --days 270

This command uses ONLY public Coinbase OHLCV candles and writes artifacts/published-strategy-research.json.

Training selects a candidate using only the first 70% of genuine contiguous six-hour candles, reserving the last 30% as untouched historical holdout. Hypothetical entries and exits fill at NEXT six-hour OPEN; modeled fees are 90 bps per side plus 10 bps round-trip spread, with a higher-cost stress case. Real user fees, realized slippage and order-book depth remain unknown. No automatic promotion.

PARTIAL and INSUFFICIENT_HISTORY are normal research outcomes. Historical OOS_PASS is a historical label, NOT verified future profitability.

## Optional persistent forward research learning

    python -m src.published_strategy_research --product BTC-USD --product ETH-USD --product SOL-USD --days 270 --learn

First invocation freezes an eligible TRAINING-selected policy at the current completed candle. Later invocations only score newer completed candles, re-evaluate all preregistered rule families using net hypothetical returns, and keep SHA-256-linked append-only SQLite observation records. Duplicate invocations do not double-count; historical backdating is blocked. Learned preferences are explicitly RESEARCH_ONLY_NOT_DEPLOYED, never connected to funded trading.

Local SQLite default: LocalAppData/AutonomousCapitalLab/published-strategy-learning.sqlite3. No watcher is scheduled: repeat the command from the actual PowerShell source checkout after later six-hour candle closes. If the original freeze is no longer within the 270-day fetch horizon, continuity is insufficient and no synthetic observations are created.

The owner's actual clean PowerShell checkout matching reviewed SHA, authenticated read-only Coinbase preflight, independent review, and validated fee-net forward performance remain separate release gates.
