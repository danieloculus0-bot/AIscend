# AIscend strategy algorithm catalog

Authored research specifications collected 2026-10-24. No strategy here has been independently validated by inclusion in this database. Thresholds are test seeds. Specialized algorithms explicitly identify the engine/data they still require.

This is a broad, finite taxonomy. It does not claim to contain every possible trading algorithm or all internet data. Variants and combinations are unbounded.

## Trend

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| sma_cross | Simple moving-average crossover | Enter: fast > slow; exit: fast <= slow | TALIB, TECH_POS, TECH_NEG |
| ema_cross | Exponential moving-average crossover | Enter: fast > slow; exit: fast <= slow | TALIB, TECH_POS, TECH_NEG |
| price_sma | Price versus long moving average | Enter: close > slow; exit: close <= slow | TALIB, TECH_POS, TECH_NEG |
| ma_envelope | Moving-average envelope filter | Enter: close > mean * 1.02; exit: close < mean | TALIB, TECH_POS, TECH_NEG |
| macd | MACD line and signal | Enter: macd > signal and macd > 0; exit: macd < signal | TALIB, TECH_POS, TECH_NEG |
| ppo | Percentage price oscillator | Enter: ppo > signal and ppo > 0; exit: ppo < signal | TALIB, TECH_POS, TECH_NEG |
| roc | Rate-of-change momentum | Enter: roc > 0.03; exit: roc <= 0 | TSM, CRYPTO_MOM, TALIB |
| vol_scaled_tsm | Volatility-scaled time-series momentum | Enter: momentum > 0 and volatility > 0; exit: momentum <= 0 | TSM, CRYPTO_MOM |
| adx_di | ADX with directional movement | Enter: adx > 25 and plus_di > minus_di; exit: adx < 20 or plus_di <= minus_di | TALIB, TECH_POS, TECH_NEG |
| aroon | Aroon trend | Enter: up > 70 and down < 30; exit: up < down | TALIB, TECH_POS, TECH_NEG |
| kama | Kaufman adaptive average | Enter: close > kama and kama > prior_kama; exit: close <= kama | TALIB, TECH_POS, TECH_NEG |
| dema | Double exponential trend | Enter: fast > slow; exit: fast <= slow | TALIB, TECH_POS, TECH_NEG |
| tema | Triple exponential trend | Enter: fast > slow; exit: fast <= slow | TALIB, TECH_POS, TECH_NEG |
| trix | TRIX trend | Enter: trix > 0; exit: trix <= 0 | TALIB, TECH_POS, TECH_NEG |
| linear_slope | Rolling regression trend | Enter: slope > 0 and r_squared > 0.5; exit: slope <= 0 | TALIB, TECH_POS, TECH_NEG |
| sar | Parabolic SAR trend | Enter: close > sar; exit: close <= sar | TALIB, TECH_POS, TECH_NEG |
| ichimoku | Ichimoku cloud filter | Enter: close > cloud_top and tenkan > kijun; exit: close < kijun | TALIB, TECH_POS, TECH_NEG |
| supertrend | ATR Supertrend | Enter: close > trailing_band; exit: close <= trailing_band | TALIB, TECH_POS, TECH_NEG |
| multi_horizon_trend | Multi-horizon trend vote | Enter: votes >= 2; exit: votes < 2 | TSM, CRYPTO_MOM |
| chandelier | Chandelier trailing trend | Enter: close > entry_high; exit: close < trailing_stop | TALIB, TECH_POS, TECH_NEG |

## Reversal

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| rsi_reversion | RSI oversold reversion | Enter: rsi < 30; exit: rsi >= 50 | TALIB, TECH_POS, TECH_NEG |
| rsi2 | Short-horizon RSI2 reversion | Enter: rsi2 < 5 and close > slow; exit: rsi2 > 70 or close < slow | TALIB, TECH_POS, TECH_NEG |
| bollinger_revert | Bollinger band reversion | Enter: close < lower; exit: close >= mean | TALIB, TECH_POS, TECH_NEG |
| zscore_revert | Rolling price z-score reversion | Enter: z < -2; exit: z >= 0 | TALIB, TECH_POS, TECH_NEG |
| stochastic_revert | Stochastic oscillator reversion | Enter: k < 20 and k > d; exit: k > 80 or k < d | TALIB, TECH_POS, TECH_NEG |
| williams_r | Williams percent-R reversion | Enter: williams < -80; exit: williams > -20 | TALIB, TECH_POS, TECH_NEG |
| cci_revert | Commodity-channel reversion | Enter: cci < -100; exit: cci >= 0 | TALIB, TECH_POS, TECH_NEG |
| cmo_revert | Chande momentum reversion | Enter: cmo < -50; exit: cmo >= 0 | TALIB, TECH_POS, TECH_NEG |
| ultimate_revert | Ultimate oscillator reversion | Enter: uo < 30; exit: uo >= 50 | TALIB, TECH_POS, TECH_NEG |
| vwap_revert | Rolling VWAP deviation reversion | Enter: z < -2 and trend_strength < 20; exit: z >= 0 or trend_strength > 30 | TALIB, TECH_POS, TECH_NEG |
| gap_revert | Large opening-gap reversion | Enter: gap < -0.05 and spread_bps < 20; exit: recovered >= 0.5 | TALIB, TECH_POS, TECH_NEG |
| shock_reversal | Volatility shock exhaustion | Enter: return_z < -3 and volume_z > 2; exit: return_z > 0 | TALIB, TECH_POS, TECH_NEG |
| streak_reversal | Consecutive down-bar reversal | Enter: down_streak >= 4 and adx < 20; exit: up_streak >= 2 | TALIB, TECH_POS, TECH_NEG |

## Breakout

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| donchian | Donchian channel breakout | Enter: close > prior_high; exit: close < prior_low | TALIB, TECH_POS, TECH_NEG |
| bollinger_breakout | Bollinger breakout continuation | Enter: close > upper and volume_ratio > 1.5; exit: close < mean | TALIB, TECH_POS, TECH_NEG |
| keltner_breakout | Keltner ATR-channel breakout | Enter: close > upper; exit: close < center | TALIB, TECH_POS, TECH_NEG |
| squeeze | Volatility compression breakout | Enter: bandwidth_rank < 0.2 and close > prior_high; exit: close < mean | TALIB, TECH_POS, TECH_NEG |
| atr_breakout | ATR-normalized range expansion | Enter: close > previous_close + 2 * atr and volume_ratio > 1.5; exit: close < previous_close | TALIB, TECH_POS, TECH_NEG |
| opening_range | UTC opening-range breakout | Enter: price > opening_high and session_ready > 0; exit: price < opening_low | TALIB, TECH_POS, TECH_NEG |
| inside_bar | Inside-bar breakout | Enter: inside > 0 and price > mother_high; exit: price < mother_low | TALIB, TECH_POS, TECH_NEG |
| failed_breakout | Failed-breakdown reclaim | Enter: swept_low > 0 and close > level; exit: close < level | TALIB, TECH_POS, TECH_NEG |

## Volume

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| obv | On-balance-volume confirmation | Enter: obv > obv_mean and close > slow; exit: obv <= obv_mean or close <= slow | TALIB, TECH_POS, TECH_NEG |
| ad_line | Accumulation-distribution confirmation | Enter: ad > ad_mean and close > mean; exit: ad < ad_mean | TALIB, TECH_POS, TECH_NEG |
| chaikin | Chaikin oscillator flow | Enter: adosc > 0 and close > mean; exit: adosc <= 0 | TALIB, TECH_POS, TECH_NEG |
| mfi | Money-flow-index reversal | Enter: mfi < 20; exit: mfi >= 60 | TALIB, TECH_POS, TECH_NEG |
| volume_breakout | Relative-volume momentum | Enter: relative_volume > 2 and return_1 > 0.02; exit: return_1 < 0 | TALIB, TECH_POS, TECH_NEG |
| vpt | Volume-price-trend confirmation | Enter: vpt_slope > 0 and momentum > 0; exit: vpt_slope <= 0 | TALIB, TECH_POS, TECH_NEG |

## Cross Section

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| xsmom | Cross-sectional momentum | Rank prior 30-day returns; long top quintile, short bottom quintile where shortable | FACTORS, CRYPTO_MOM |
| xs_reversal | Cross-sectional short-term reversal | Rank prior 1-day returns; long bottom quintile, short top quintile | FACTORS, CRYPTO_MOM |
| relative_strength_rotation | Long-only relative-strength rotation | Rank prior 30-day returns; hold top five eligible liquid assets and cash if all ranks have negative raw return | FACTORS, CRYPTO_MOM |
| size_factor | Small-minus-large crypto factor | Rank point-in-time circulating market cap; equal-dollar long small and short large with minimum liquidity filter | FACTORS |
| lowvol_factor | Low-volatility selection | Rank trailing30 realized volatility; long lowest quintile and rebalance weekly | LOWVOL, RISK_PORT |
| liquidity_factor | Liquidity characteristic portfolio | Rank prior30 dollar volume or Amihud abs(return)/dollar-volume; test both directions as separate preregistered trials | FACTORS |
| beta_neutral_momentum | Beta-neutral momentum basket | Estimate rolling90 beta to crypto market; rank momentum and solve beta/weight-neutral long-short constraints | FACTORS, CRYPTO_MOM |
| residual_momentum | Factor-residual momentum | Fit rolling market/size/momentum regression on training window; rank cumulative residual returns | FACTORS |
| sector_relative | Narrative-sector relative strength | Use versioned category membership; rank sectors by lagged30 return; hold strongest eligible sector basket | NARRATIVE, GECKO |
| breadth_regime | Market-breadth allocation | Fraction of eligible assets above SMA50 sets gross long exposure; equal-weight selected positive-trend assets | CRYPTO_MOM, RISK_PORT |
| dominance_rotation | BTC dominance rotation | Trade BTC versus equal-risk altcoin basket on lagged dominance trend; include circulating-supply vintages | FACTORS, CM |
| dispersion_revert | Cross-sectional dispersion reversion | When residual dispersion is above training90 percentile, buy residual losers and short residual winners | FACTORS, PAIR_COMPARE |

## Stat Arb

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| pair_distance | Distance pairs | Normalize two training-window price paths to one; select smallest squared distance | PAIR_COMPARE, COINT, RF_ARB |
| pair_engle_granger | Engle-Granger cointegration | Fit log(A)=alpha+beta*log(B) on trailing training window; test residual stationarity with fixed test level | PAIR_COMPARE, COINT, RF_ARB |
| pair_johansen | Johansen basket cointegration | Fit vector error-correction model and rank using only formation data | PAIR_COMPARE, COINT, RF_ARB |
| pair_kalman | Kalman dynamic-hedge pair | Initialize hedge-state covariance on training data | PAIR_COMPARE, COINT, RF_ARB |
| pair_ou | Ornstein-Uhlenbeck spread timing | Fit mean-reversion rate/theta/sigma on training spread; reject nonpositive rate | PAIR_COMPARE, COINT, RF_ARB |
| pair_copula | Copula mispricing pair | Fit marginal CDFs and copula on training returns | PAIR_COMPARE, COINT, RF_ARB |
| pca_residual | PCA residual basket | Fit principal components on trailing standardized returns | PAIR_COMPARE, COINT, RF_ARB |
| cluster_reversion | Clustered residual reversion | Cluster assets by training return correlations | PAIR_COMPARE, COINT, RF_ARB |
| network_leadlag | Cross-asset lead-lag | Estimate lead/lag coefficients with lagged training regressions and multiple-test correction | PAIR_COMPARE, COINT, RF_ARB |
| rf_relative_120m | Random-forest relative-performance ranking | Train RF on lagged multi-coin returns to classify next120-minute performance above cross-sectional median | PAIR_COMPARE, COINT, RF_ARB |

## Carry Arbitrage

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| cross_venue_arb | Prefunded cross-exchange arbitrage | Maintain prefunded inventories on both venues | CARRY, LATENCY, PERP, DERIBIT |
| triangular_arb | Triangular same-venue arbitrage | Build currency graph with executable bid/ask edges after fee | CARRY, LATENCY, PERP, DERIBIT |
| multi_hop_arb | Multi-hop currency-cycle arbitrage | Transform after-fee executable exchange rates to negative-log edge weights | CARRY, LATENCY, PERP, DERIBIT |
| cash_carry | Dated-futures cash and carry | Compute basis=F/S-1 and days-to-maturity | CARRY, LATENCY, PERP, DERIBIT |
| reverse_carry | Reverse cash and carry | When dated future is cheap beyond borrow/financing costs, short borrowable spot and buy matched future | CARRY, LATENCY, PERP, DERIBIT |
| perp_funding_carry | Spot-perpetual funding carry | Estimate next funding only from information published before decision | CARRY, LATENCY, PERP, DERIBIT |
| cross_perp_funding | Cross-venue perpetual funding differential | Synchronize contract multipliers, funding calendars and collateral currency | CARRY, LATENCY, PERP, DERIBIT |
| calendar_basis | Futures calendar-spread convergence | Compute near/far fair financing-adjusted calendar spread using matched specs | CARRY, LATENCY, PERP, DERIBIT |
| index_basis | Perpetual premium dislocation | Compute executable perp premium to replicable spot-index basket | CARRY, LATENCY, PERP, DERIBIT |
| stablecoin_arb | Stablecoin cross-market parity | Compare stablecoin executable conversions and redemption economics | CARRY, LATENCY, PERP, DERIBIT |
| wrapped_asset_arb | Wrapped-versus-native parity | Compute native/wrapped exchange ratio after redemption/bridge costs and delay | CARRY, LATENCY, PERP, DERIBIT |
| lst_basis | Liquid-staking-token discount convergence | Normalize LST by time-varying redemption/exchange rate | CARRY, LATENCY, PERP, DERIBIT |

## Market Making

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| fixed_spread_mm | Fixed-spread inventory-capped market making | Quote bid=mid*(1-s), ask=mid*(1+s), with fee-adjusted s | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| inventory_skew_mm | Inventory-skewed market making | Compute deviation q from target inventory | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| avellaneda_stoikov | Avellaneda-Stoikov market making | Calibrate volatility and fill-intensity decay k from past order/trade events | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| xemm | Cross-exchange maker/taker hedge | Quote maker side relative to depth-weighted taker hedge after both fees | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| microprice_mm | Microprice-informed quotes | Estimate microprice=(ask*bid_size+bid*ask_size)/(bid_size+ask_size) from known L1 | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| order_imbalance | Orderbook-imbalance directional signal | I=(bid_depth-ask_depth)/(bid_depth+ask_depth) for fixed first5 levels | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| ofi | Order-flow-imbalance signal | Accumulate signed changes in best bid/ask prices and queues over last fixed event window | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| trade_flow | Aggressor trade-flow imbalance | Classify actual aggressor-side trades or use a fixed causal tick rule | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| toxic_flow_mm | Toxicity-aware market-making pause | Estimate adverse markout of recent fills using only already-matured markouts | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| adaptive_grid | Volatility-scaled bounded grid | Freeze range center and spacing multiple of trailing ATR | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| geometric_grid | Geometric price grid | Set prices p_j=p0*(1+g)^j within fixed funded range | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |
| linear_grid | Arithmetic price grid | Set evenly spaced funded price levels | AS, AS_IMPL, PMM, XEMM, EXEC, LATENCY |

## Options

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| vol_risk_premium | Delta-hedged variance-risk-premium harvest | Estimate future realized variance on past data; compute option-implied variance with correct units | VRP, OPTION_PREMIA, DERIBIT, PERP |
| long_straddle | Long straddle for underpriced volatility | Buy matched-expiry ATM call and put when predicted realized variance exceeds implied variance plus costs | VRP, OPTION_PREMIA, DERIBIT, PERP |
| covered_call | Covered-call overwrite | Hold spot units matching sold calls' contract multiplier | VRP, OPTION_PREMIA, DERIBIT, PERP |
| cash_secured_put | Cash-secured put | Reserve settlement collateral for full put exercise | VRP, OPTION_PREMIA, DERIBIT, PERP |
| collar | Protective collar | Hold spot, buy lower-strike put and sell upper-strike call at same expiry | VRP, OPTION_PREMIA, DERIBIT, PERP |
| protective_put | Protective-put hedge | Hold spot and buy matched-expiry downside put | VRP, OPTION_PREMIA, DERIBIT, PERP |
| bull_call_spread | Bull call vertical | Buy lower-strike call and sell higher-strike same-expiry call | VRP, OPTION_PREMIA, DERIBIT, PERP |
| bear_put_spread | Bear put vertical | Buy higher-strike put and sell lower-strike same-expiry put | VRP, OPTION_PREMIA, DERIBIT, PERP |
| iron_condor | Defined-risk iron condor | Sell OTM call/put and buy farther OTM wings at same expiry | VRP, OPTION_PREMIA, DERIBIT, PERP |
| butterfly | Option butterfly | Buy one lower strike and one upper strike, sell two middle strikes with equal spacing and expiry | VRP, OPTION_PREMIA, DERIBIT, PERP |
| calendar_option | Vega-matched calendar spread | Compare same-strike near/far IV with forecast term structure | VRP, OPTION_PREMIA, DERIBIT, PERP |
| skew_risk_reversal | Delta-neutral skew risk reversal | Compare equal-delta put/call IV difference with lagged own-history distribution | VRP, OPTION_PREMIA, DERIBIT, PERP |
| gamma_scalp | Long-gamma delta rebalancing | Buy option gamma when expected realized variation supports premium and hedge costs | VRP, OPTION_PREMIA, DERIBIT, PERP |
| put_call_parity | Put-call-parity relative value | For each matching strike/expiry derive parity under correct linear/inverse settlement and rates | VRP, OPTION_PREMIA, DERIBIT, PERP |
| vol_dispersion | BTC-altcoin volatility dispersion | Estimate basket weights and implied correlations from available chains | VRP, OPTION_PREMIA, DERIBIT, PERP |

## Defi

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| lp_constant_product | Passive constant-product liquidity provision | Deposit specified two-token weights into x*y=k pool | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| lp_concentrated_static | Static concentrated-liquidity range | Choose tick range from trailing volatility before deposit | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| lp_dynamic_range | Dynamic volatility-based LP range | Estimate volatility and expected fee revenue from past pool events | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| lp_delta_hedged | Delta-hedged LP position | Compute LP delta from current reserves/range | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| stable_pool_lp | Stablecoin-pool liquidity | Select pool with verified coins and stress redemption assumptions | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| lending_carry | Collateralized on-chain lending carry | Compare forward supply yield net of incentives dilution/gas to cash alternative | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| yield_rotation | Risk-adjusted yield rotation | Rank net lending/staking yields after gas and protocol concentration using only available snapshots | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| staking_hedged | Staking yield with market hedge | Stake base token; short matched derivative delta if accessible | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| dex_cex_arb | DEX-CEX executable-price arbitrage | Compare exact AMM swap output including fee/gas with executable CEX depth | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| dex_route_arb | Atomic DEX route arbitrage | Enumerate public pool routes with current reserves | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| liquidation_keeper | Public-protocol liquidation keeper | Read public liquidation eligibility from confirmed protocol state | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |
| gas_timing | Gas-aware delayed rebalance | Estimate fee distribution from prior blocks | LVR, LP_NEG, IL_NEG, LP_RANGE, LP_OPT, LLAMA |

## Onchain

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| exchange_flow | Exchange net-flow signal | Enter: flow_z < -2; exit: flow_z >= 0 | FLOWS, FACTORS, CM, LLAMA |
| stablecoin_supply | Stablecoin-supply impulse | Enter: growth_z > 2 and trend > 0; exit: growth_z < 0 | FLOWS, FACTORS, CM, LLAMA |
| nvt_value | Network-value-to-transactions value | Enter: nvt_z < -1.5; exit: nvt_z > 0.5 | FLOWS, FACTORS, CM, LLAMA, NVT |
| mvrv | MVRV valuation | Enter: mvrv < 1; exit: mvrv > 2 | FLOWS, FACTORS, CM, LLAMA, MVRV |
| sopr | Spent-output profitability regime | Enter: sopr > 1 and previous_sopr <= 1; exit: sopr < 1 | FLOWS, FACTORS, CM, LLAMA, SOPR |
| active_addresses | Network-adoption growth | Enter: growth > 0.1 and price_trend > 0; exit: growth <= 0 | FLOWS, FACTORS, CM, LLAMA |
| hash_recovery | Hash-rate recovery | Enter: fast_hash > slow_hash and trend > 0; exit: fast_hash < slow_hash | FLOWS, FACTORS, CM, LLAMA |
| miner_pressure | Miner-flow pressure | Enter: miner_outflow_z < -1 and trend > 0; exit: miner_outflow_z > 2 | FLOWS, FACTORS, CM, LLAMA |
| fee_revenue | Protocol-fee growth | Enter: revenue_growth > 0.2 and valuation_z < 1; exit: revenue_growth < 0 | FLOWS, FACTORS, CM, LLAMA |
| whale_concentration | Large-holder accumulation | Enter: accumulation_z > 2; exit: accumulation_z < 0 | FLOWS, FACTORS, CM, LLAMA |
| tvl_flow | Price-adjusted TVL growth | Enter: tvl_growth > 0.1 and trend > 0; exit: tvl_growth < 0 | FLOWS, FACTORS, CM, LLAMA |
| exchange_reserves | Exchange-reserve depletion | Enter: reserve_change < -0.05 and trend > 0; exit: reserve_change > 0.05 | FLOWS, FACTORS, CM, LLAMA |

## Narrative

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| news_sentiment_mom | News sentiment continuation | Enter: sentiment > 0.3 and attention_z > 2 and diversity >= 2; exit: sentiment <= 0 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| news_sentiment_fade | Extreme-sentiment fade | Enter: sentiment > 0.7 and prior_return > 0.1 and fatigue > 0.5; exit: sentiment < 0.2 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| negative_news_rebound | Negative-news exhaustion rebound | Enter: sentiment < -0.7 and recovery > 0; exit: sentiment >= 0 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| narrative_rotation | Narrative theme rotation | Enter: acceleration > 2 and relative_strength > 0 and diversity >= 2; exit: acceleration < 0 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| cross_source_alignment | Independent news/social alignment | Enter: news > 0.2 and social > 0.2 and diversity >= 2; exit: news * social <= 0 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| attention_price_divergence | Attention-price divergence | Enter: attention_z > 2 and prior_return <= 0 and sentiment > 0.2; exit: attention_z < 0 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| explicit_crowd | Explicit crowd buy/sell recommendations | Enter: buy_share > 0.7 and diversity >= 3; exit: buy_share < 0.5 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| fear_greed_contra | Fear-and-greed contrarian | Enter: fear_greed < 20; exit: fear_greed > 60 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| fear_greed_trend | Fear-and-greed trend confirmation | Enter: fear_greed > 60 and price_trend > 0; exit: fear_greed < 45 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| rumor_confirmation | Rumor-to-official confirmation | Enter: confirmed > 0 and surprise > 0.5; exit: contradicted > 0 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| sentiment_hawkes | Text-conditioned jump-risk overlay | Enter: up_intensity > down_intensity * 1.5; exit: down_intensity >= up_intensity | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |
| multimodal_news | Text-plus-market forecast fusion | Enter: net_edge > 0 and calibrated_probability > 0.6; exit: net_edge <= 0 | NARRATIVE, CROWD, FRAGMENT, PREBIT, NEWS_SIGNAL, HAWKES_TEXT, FINBERT, CRYPTOBERT, FNG |

## Event

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| listing_drift | Exchange-listing announcement drift | Read official listing notice, observed_at and first trading availability | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| listing_reversal | Post-listing reversal | Measure rally from announcement to first eligible entry without future peak selection | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| delisting | Delisting risk avoidance/fade | Read verified delisting timeline | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| token_unlock | Known token-unlock supply shock | Load versioned vesting schedule known before event; unlock/freefloat ratio | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| halving | Halving event window | Derive confirmed future issuance schedule and expected date from information known then | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| upgrade | Protocol-upgrade catalyst | Record scheduled upgrade, revision history and confirmation | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| etf_event | ETF approval/flow event | Use official decision/release timestamp and actual public flow release time | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| exploit_event | Hack/exploit risk response | Confirm affected contract/asset and official evidence | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| depeg_event | Stablecoin-depeg risk response | Detect executable peg breach across independent venues for fixed duration | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| governance_event | Governance vote surprise | Store vote start/end, quorum, current tally and official final outcome by known timestamps | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| funding_squeeze | Crowded-funding liquidation squeeze | Combine excessive funding, rising open interest and opposite price break from data known before entry | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |
| airdrop_distribution | Airdrop-distribution supply event | Record official claim/distribution schedule and eligible token venue | LISTING, NARRATIVE, SEC, TOKENOMIST, GECKO |

## Macro

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| rates_surprise | Interest-rate surprise response | Use official release time and consensus captured before release | FRED, ALFRED, REGIME, DSR |
| inflation_surprise | Inflation surprise response | Join first-release CPI/PCE data to as-of consensus vintage | FRED, ALFRED, REGIME, DSR |
| dollar_risk | Dollar/risk-asset regime filter | Fit rolling crypto sensitivity to lagged dollar index and equity returns on past data | FRED, ALFRED, REGIME, DSR |
| liquidity_macro | Macro-liquidity regime | Use as-released money/reserve/balance-sheet data, actual release lag and no revised lookahead | FRED, ALFRED, REGIME, DSR |
| calendar_seasonality | UTC calendar/time-of-week effect | Estimate hour/day seasonal returns on training segment with many-test correction | FRED, ALFRED, REGIME, DSR, PERIODICITY |
| cross_asset_risk | Cross-asset volatility shock filter | Compute lagged equity/bond/FX volatility shocks using synchronized availability | FRED, ALFRED, REGIME, DSR |

## Models

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| logistic | Logistic directional classifier | Build lagged returns/volatility/volume and causally available narrative features | ML_CLASS, ML_CHANGE |
| ridge_return | Ridge expected-return regression | Fit ridge regression to horizon returns using rolling past data | ML_CHANGE, ML_COST |
| elastic_net | Elastic-net sparse predictor | Create lagged candidate features and train-only scales | ML_CHANGE, ML_COST |
| random_forest | Random-forest directional model | Construct past-only feature vectors and fixed horizon matured labels | RF_ARB, ML_CLASS |
| gradient_boost | Gradient-boosted return model | Fit boosting on lagged tabular features with chronological early stopping | ML_CLASS, ML_COST |
| svm | Support-vector trading model | Scale past features with training means/std | ML_CHANGE |
| mlp | Feedforward neural trading model | Use a fixed lagged feature vector and matured label horizon | ML_CLASS, ML_COST |
| lstm | LSTM sequence forecast | Construct trailing sequence windows with no future scaling | ML_CLASS, FINRL, PREBIT |
| cnn_temporal | Temporal convolutional price/text model | Feed only trailing market and frozen-model text embeddings | PREBIT, ML_CLASS |
| transformer_sequence | Causal transformer forecast | Apply causal mask to trailing price/text sequences and train-only normalization | PREBIT, FINBERT, ML_COST |
| hmm_regime | Hidden-Markov regime selector | Fit HMM on past returns/volatility only | REGIME |
| garch_risk | GARCH volatility-targeted exposure | Fit GARCH(1,1) to prior returns with fixed refit calendar | REGIME, RISK_PORT |
| kalman_trend | Kalman local-trend forecast | Initialize state/process/observation covariance from past window | REGIME, ML_CHANGE |
| q_learning | Tabular Q-learning strategy selection | State encodes lagged regime, cash/position, prior actions and matured fee-net outcomes | RL_TRADE, FINRL, RL_LIMITS |
| context_bandit | Contextual bandit strategy allocation | Use context available now and select one registered expert/cash | FINRL, RL_TRADE, RL_LIMITS |
| ppo_policy | PPO bounded-allocation policy | Environment contains actual cash, positions, costs and trailing features | FINRL, FINRL_CRYPTO, RL_LIMITS |
| sac_policy | SAC continuous portfolio policy | Define bounded target-weight actions and executable next-observation fills | FINRL, FINRL_CRYPTO, RL_LIMITS |
| dqn_policy | DQN discrete allocation policy | Use cash/long/reduce discrete actions with a complete cash ledger | FINRL, RL_LIMITS |
| risk_meta_label | Meta-label primary signals | Freeze simple primary signal and construct entry candidates | ML_CLASS, PBO |
| ensemble | Past-performance expert ensemble | Collect lagged predictions from registered models and keep failed experts in archive | ML_CLASS, DSR |
| online_newton | Online Newton portfolio selection | Observe previous period price-relative vector x | ONS |
| pamr | Passive-aggressive mean-reversion portfolio | Observe completed price-relative vector x and current weights w | PAMR, ONS |
| retrieval_policy | BEAN evidence-retrieval strategy selection | Retrieve only experiment records finished before decision as_of, matching strategy/data/regime | FINRL, PBO, DSR |

## Allocation

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| equal_weight | Periodic equal-weight portfolio | Select historically eligible liquid universe | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| market_cap_weight | Market-cap-weighted portfolio | Use point-in-time circulating market caps for eligible assets | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| inverse_vol | Inverse-volatility allocation | Estimate trailing30 daily volatility | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| risk_parity | Equal-risk-contribution portfolio | Estimate training/rolling covariance with shrinkage | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| minimum_variance | Minimum-variance portfolio | Estimate shrinkage covariance from trailing90 returns | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| max_diversification | Maximum-diversification portfolio | Estimate trailing volatility vector and shrinkage covariance | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| hierarchical_risk | Hierarchical risk-parity allocation | Cluster assets using lagged correlation distance | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| mean_variance | Regularized mean-variance allocation | Estimate shrunk expected returns/covariance using past data | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| cvar_portfolio | Conditional-value-at-risk portfolio | Build trailing stress-return scenarios with fixed probabilities | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| drawdown_minmax | Minmax drawdown allocation | Maintain equity high-water mark and allowed drawdown budget | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| momentum_risk_parity | Trend-filtered risk parity | Select eligible positive-trend assets using fixed trailing horizon | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |
| universal_portfolio | Finite universal-portfolio mixture | Predeclare a grid of capped constant-rebalanced portfolios | RISK_PORT, DRAWDOWN, ONS, CRYPTO_MOM |

## Execution

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| twap | Time-weighted execution | Divide fixed parent quantity across N future time slices | EXEC, PMM, LATENCY |
| vwap_execution | Predicted-volume-weighted execution | Estimate volume curve using only previous sessions | EXEC, PMM, LATENCY |
| participation | Participation-of-volume execution | Set maximum fraction of observed market traded volume per interval | EXEC, PMM, LATENCY |
| implementation_shortfall | Risk/impact-aware execution | Estimate past volatility and impact curve | EXEC, PMM, LATENCY |
| passive_aggressive_switch | Passive-to-aggressive execution | Start funded post-only limit order within spread | EXEC, PMM, LATENCY |
| iceberg | Visible-size-limited child execution | Split parent into bounded visible child orders | EXEC, PMM, LATENCY |

## Control

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| vol_target | Volatility-target overlay | Estimate annualized realized volatility from prior returns | DRAWDOWN, RISK_PORT, PBO, DSR |
| fractional_kelly | Fractional Kelly sizing | Estimate conditional expected net return mu and variance sigma2 on past matured outcomes | DRAWDOWN, RISK_PORT, PBO, DSR |
| atr_stop | ATR stop and fixed-risk sizing | Place hypothetical stop k*past_ATR from expected entry | DRAWDOWN, RISK_PORT, PBO, DSR |
| triple_barrier | Triple-barrier exit | At confirmed entry freeze upper/lower barriers scaled by trailing volatility and maximum time | DRAWDOWN, RISK_PORT, PBO, DSR |
| drawdown_brake | Drawdown exposure brake | Track marked-to-liquidation equity and peak including open positions | DRAWDOWN, RISK_PORT, PBO, DSR |
| cost_no_trade | Cost-aware no-trade band | Estimate expected advantage of target versus current position | DRAWDOWN, RISK_PORT, PBO, DSR |
| regime_stop | Change-point model pause | Monitor past prediction errors/costs with a frozen sequential detector | DRAWDOWN, RISK_PORT, PBO, DSR |
| martingale_capped | Capped loss-doubling experiment | Begin fixed tiny research stake and double after each realized loss up to a frozen max3 doublings | DRAWDOWN, RISK_PORT, PBO, DSR |
| anti_martingale | Capped win-pyramiding | Increase exposure after matured wins by fixed factor under unchanged gross/risk caps | DRAWDOWN, RISK_PORT, PBO, DSR |

## Baseline

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| cash | Cash baseline | Hold cash with explicitly modeled currency/interest/counterparty assumptions | RISK_PORT, EXEC, ONS |
| buy_hold_btc | BTC buy-and-hold baseline | Buy cash-funded BTC at first eligible executable quote | RISK_PORT, EXEC, ONS |
| buy_hold_basket | Fixed crypto-basket buy and hold | Freeze historically eligible basket and weights at test start | RISK_PORT, EXEC, ONS |
| dca_calendar | Calendar dollar-cost averaging | Schedule fixed external contribution and purchase dates in advance | RISK_PORT, EXEC, ONS |
| value_average | Capped value averaging | Define target portfolio value path from explicit contributions | RISK_PORT, EXEC, ONS |
| threshold_rebalance | Threshold constant-mix rebalance | Freeze target weights | RISK_PORT, EXEC, ONS |

## Patterns

| ID | Algorithm | Inputs / executable rule | Sources |
| --- | --- | --- | --- |
| pattern_cdl2crows | CDL2CROWS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdl3blackcrows | CDL3BLACKCROWS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdl3inside | CDL3INSIDE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdl3linestrike | CDL3LINESTRIKE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdl3outside | CDL3OUTSIDE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdl3starsinsouth | CDL3STARSINSOUTH signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdl3whitesoldiers | CDL3WHITESOLDIERS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlabandonedbaby | CDLABANDONEDBABY signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdladvanceblock | CDLADVANCEBLOCK signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlbelthold | CDLBELTHOLD signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlbreakaway | CDLBREAKAWAY signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlclosingmarubozu | CDLCLOSINGMARUBOZU signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlconcealbabyswall | CDLCONCEALBABYSWALL signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlcounterattack | CDLCOUNTERATTACK signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdldarkcloudcover | CDLDARKCLOUDCOVER signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdldoji | CDLDOJI signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdldojistar | CDLDOJISTAR signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdldragonflydoji | CDLDRAGONFLYDOJI signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlengulfing | CDLENGULFING signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdleveningdojistar | CDLEVENINGDOJISTAR signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdleveningstar | CDLEVENINGSTAR signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlgapsidesidewhite | CDLGAPSIDESIDEWHITE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlgravestonedoji | CDLGRAVESTONEDOJI signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlhammer | CDLHAMMER signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlhangingman | CDLHANGINGMAN signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlharami | CDLHARAMI signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlharamicross | CDLHARAMICROSS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlhighwave | CDLHIGHWAVE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlhikkake | CDLHIKKAKE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlhikkakemod | CDLHIKKAKEMOD signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlhomingpigeon | CDLHOMINGPIGEON signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlidentical3crows | CDLIDENTICAL3CROWS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlinneck | CDLINNECK signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlinvertedhammer | CDLINVERTEDHAMMER signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlkicking | CDLKICKING signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlkickingbylength | CDLKICKINGBYLENGTH signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlladderbottom | CDLLADDERBOTTOM signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdllongleggeddoji | CDLLONGLEGGEDDOJI signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdllongline | CDLLONGLINE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlmarubozu | CDLMARUBOZU signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlmatchinglow | CDLMATCHINGLOW signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlmathold | CDLMATHOLD signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlmorningdojistar | CDLMORNINGDOJISTAR signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlmorningstar | CDLMORNINGSTAR signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlonneck | CDLONNECK signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlpiercing | CDLPIERCING signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlrickshawman | CDLRICKSHAWMAN signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlrisefall3methods | CDLRISEFALL3METHODS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlseparatinglines | CDLSEPARATINGLINES signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlshootingstar | CDLSHOOTINGSTAR signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlshortline | CDLSHORTLINE signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlspinningtop | CDLSPINNINGTOP signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlstalledpattern | CDLSTALLEDPATTERN signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlsticksandwich | CDLSTICKSANDWICH signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdltakuri | CDLTAKURI signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdltasukigap | CDLTASUKIGAP signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlthrusting | CDLTHRUSTING signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdltristar | CDLTRISTAR signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlunique3river | CDLUNIQUE3RIVER signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlupsidegap2crows | CDLUPSIDEGAP2CROWS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| pattern_cdlxsidegap3methods | CDLXSIDEGAP3METHODS signed-candle template | Enter: pattern_score != 0; exit: opposite_pattern > 0 or held_bars >= 5 | TALIB, TECH_POS, TECH_NEG, PBO |
| double_bottom | Confirmed double-bottom pattern | Confirm local extrema only after k=3 right-side completed bars | TALIB, TECH_POS, TECH_NEG, PBO |
| double_top | Confirmed double-top pattern | Confirm extrema after3 right-side bars | TALIB, TECH_POS, TECH_NEG, PBO |
| head_shoulders | Confirmed head-and-shoulders | Use only pivots whose3-bar right confirmation has elapsed | TALIB, TECH_POS, TECH_NEG, PBO |
| inverse_head_shoulders | Confirmed inverse head-and-shoulders | Confirm three troughs after3 later bars, middle lowest, outside lows within5% | TALIB, TECH_POS, TECH_NEG, PBO |
| triangle_break | Confirmed triangle consolidation | Use last3 confirmed descending highs and ascending lows from prior50 bars | TALIB, TECH_POS, TECH_NEG, PBO |
| flag_break | Measured flag continuation | Require prior10-bar rise exceeding3 ATR, then5-15-bar consolidation retracing less than50% | TALIB, TECH_POS, TECH_NEG, PBO |
| fibonacci_retrace | Confirmed-swing Fibonacci retracement | Confirm swing low/high after3-bar pivot delay | TALIB, TECH_POS, TECH_NEG, PBO |
| harmonic_abcd | Causally confirmed ABCD ratio pattern | Extract alternating confirmed pivots with fixed3-bar delay | TALIB, TECH_POS, TECH_NEG, PBO |
| elliott_fsm | Finite-state swing-wave hypothesis | Define pivots by fixed ATR reversal with elapsed confirmation | TALIB, TECH_POS, TECH_NEG, PBO |
| support_resistance | Repeated-level breakout | Cluster only confirmed prior pivots within one trailing ATR | TALIB, TECH_POS, TECH_NEG, PBO |

## Source register

| ID | Primary source | Result/evidence scope |
| --- | --- | --- |
| TSM | [Time series momentum](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf) | positive_in_published_sample |
| CRYPTO_MOM | [Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market: A Comprehensive Analysis under Realistic Assumptions](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf) | mixed_and_negative |
| TECH_POS | [Technical trading and cryptocurrencies](https://link.springer.com/article/10.1007/s10479-019-03357-1) | positive_in_published_sample |
| TECH_NEG | [Profitability and Data-Snooping Tests of Four Technical Trade Strategies for BTC/USDT and ETH/USDT During 2022-2023](https://doi.org/10.33423/jaf.v23i4.6453) | negative_in_published_sample |
| RF_ARB | [Statistical Arbitrage in Cryptocurrency Markets](https://www.mdpi.com/1911-8074/12/1/31) | positive_in_published_sample |
| FACTORS | [Common Risk Factors in Cryptocurrency](https://www.nber.org/papers/w25882) | positive_in_published_sample |
| CARRY | [Crypto carry](https://www.bis.org/publ/work1087.htm) | mixed_and_negative |
| LATENCY | [Building Trust Takes Time: Limits to Arbitrage for Blockchain-Based Assets](https://arxiv.org/html/1812.00595v4) | mixed_and_negative |
| AS | [High-frequency trading in a limit order book](https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf) | theoretical |
| PMM | [Hummingbot Pure Market Making](https://hummingbot.org/strategies/v1-strategies/pure-market-making/) | not_a_profit_test |
| XEMM | [Hummingbot Cross-Exchange Market Making](https://hummingbot.org/strategies/v1-strategies/cross-exchange-market-making/) | not_a_profit_test |
| AS_IMPL | [Hummingbot Avellaneda-Stoikov implementation](https://hummingbot.org/blog/technical-deep-dive-into-the-avellaneda--stoikov-strategy/) | not_a_profit_test |
| EXEC | [Hummingbot V2 Executors](https://hummingbot.org/strategies/v2-strategies/executors/) | not_a_profit_test |
| TALIB | [TA-Lib Function List](https://ta-lib.github.io/function.html) | indicator_definitions_only |
| PAIR_COMPARE | [Pairs trading in cryptocurrency markets: A comparative study of statistical methods](https://www.tandfonline.com/doi/full/10.1080/10293523.2023.2268386) | method_and_sample_dependent |
| COINT | [Constructing cointegrated cryptocurrency portfolios for statistical arbitrage](https://www.emerald.com/sef/article/36/4/581/353687/Constructing-cointegrated-cryptocurrency) | positive_in_published_sample |
| PERP | [Perpetual Futures Pricing](https://arxiv.org/abs/2310.11771) | theoretical |
| VRP | [The Bitcoin VIX and its Variance Risk Premium](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3383734) | descriptive_not_strategy_validation |
| OPTION_PREMIA | [Risk Premia in the Bitcoin Market](https://arxiv.org/abs/2410.15195) | descriptive_not_strategy_validation |
| LVR | [Automated Market Making and Loss-Versus-Rebalancing](https://arxiv.org/abs/2208.06046) | theoretical |
| LP_NEG | [Measuring Arbitrage Losses and Profitability of AMM Liquidity](https://arxiv.org/html/2404.05803v2) | negative_in_published_sample |
| IL_NEG | [Impermanent Loss in Uniswap v3](https://arxiv.org/abs/2111.09192) | negative_in_published_sample |
| LP_RANGE | [Strategic Liquidity Provision in Uniswap v3](https://arxiv.org/abs/2106.12033) | theoretical_and_empirical |
| LP_OPT | [Decentralised Finance and Automated Market Making: Predictable Loss and Optimal Liquidity Provision](https://arxiv.org/abs/2309.08431) | theoretical |
| RISK_PORT | [Risk-Based Portfolio Optimization in the Cryptocurrency World](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3454764) | method_and_sample_dependent |
| DRAWDOWN | [Managing a Crypto-Currency Portfolio via MinMax Drawdown Control](https://www.sylvainchassang.org/assets/papers/crypto_portfolio_management.pdf) | theoretical_and_empirical |
| LOWVOL | [Low-volatility strategies for highly liquid cryptocurrencies](https://www.sciencedirect.com/science/article/pii/S1544612321004116) | positive_in_published_sample |
| ML_CHANGE | [Forecasting and trading cryptocurrencies with machine learning under changing market conditions](https://pmc.ncbi.nlm.nih.gov/articles/PMC7785332/) | method_and_sample_dependent |
| ML_CLASS | [Machine learning for cryptocurrency market prediction and trading](https://www.sciencedirect.com/science/article/pii/S2405918822000174) | positive_in_published_sample |
| ML_COST | [Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting](https://arxiv.org/html/2606.00060) | method_and_sample_dependent |
| RL_TRADE | [Cryptocurrency Trading Using Machine Learning](https://www.mdpi.com/1911-8074/13/8/178) | positive_in_published_sample |
| RL_LIMITS | [Reinforcement Learning in Financial Markets](https://www.mdpi.com/2306-5729/4/3/110) | negative_methodology_evidence |
| FINRL | [FinRL-Meta: Market Environments and Benchmarks](https://arxiv.org/html/2211.03107v1) | not_a_profit_test |
| FINRL_CRYPTO | [FinRL_Crypto](https://github.com/AI4Finance-Foundation/FinRL_Crypto) | author_reported_not_reproduced |
| REGIME | [Regime switching forecasting for cryptocurrencies](https://link.springer.com/article/10.1007/s42521-024-00123-2) | method_and_sample_dependent |
| ONS | [Damped Online Newton Step for Portfolio Selection](https://proceedings.mlr.press/v178/mhammedi22b.html) | theoretical |
| PAMR | [PAMR: Passive Aggressive Mean Reversion Strategy for Portfolio Selection](https://mlanthology.org/mlj/2012/li2012mlj-pamr/) | theoretical_and_empirical |
| NARRATIVE | [Narrative attention and related cryptocurrency returns](https://www.sciencedirect.com/science/article/abs/pii/S1544612323005469) | association_with_bidirectionality |
| CROWD | [Wisdom of the crowd signals: Predictive power of social media trading signals for cryptocurrencies](https://link.springer.com/article/10.1007/s12525-025-00815-6) | mixed_and_positive_in_sample |
| FRAGMENT | [Signal Fragmentation in Cryptocurrency Markets](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4495683) | association_not_causal_profit |
| PREBIT | [PreBit: A multimodal model with Twitter FinBERT embeddings](https://arxiv.org/abs/2206.00648) | positive_in_published_sample |
| NEWS_SIGNAL | [Exploring Relationships Between Cryptocurrency News Outlets and Influencers' Twitter Activity and Market Prices](https://arxiv.org/abs/2411.05577) | association_not_causal_profit |
| HAWKES_TEXT | [Attention-amplified sentiment and jump risk in Bitcoin and Ethereum markets](https://pure.eur.nl/ws/portalfiles/portal/269122832/1-s2.0-S1057521926003583-main.pdf) | forecast_likelihood_not_profit |
| LISTING | [Market Reaction to Exchange Listings of Cryptocurrencies](https://www.blockchainresearchlab.org/wp-content/uploads/2019/10/Exploring-Market-Reactions-to-Exchange-Listings-of-Cryptocurrencies-BRL-working-paper3.pdf) | association_not_causal_profit |
| FLOWS | [Return and Volatility Forecasting Using On-Chain Flows in Cryptocurrency Markets](https://arxiv.org/abs/2411.06327) | asset_dependent |
| FINBERT | [FinBERT: Financial Sentiment Analysis with Pre-trained Language Models](https://arxiv.org/abs/1908.10063) | nlp_not_profit_test |
| FINBERT_CARD | [ProsusAI/finbert model card](https://huggingface.co/ProsusAI/finbert) | nlp_not_profit_test |
| CRYPTOBERT | [CryptoBERT model card](https://huggingface.co/ElKulako/cryptobert) | nlp_not_profit_test |
| PBO | [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | negative_methodology_evidence |
| DSR | [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | negative_methodology_evidence |
| CM | [Coin Metrics Community data archive](https://github.com/coinmetrics/data) | not_a_profit_test |
| CM_LICENSE | [Coin Metrics data license](https://github.com/coinmetrics/data/blob/master/LICENSE) | not_a_profit_test |
| CM_API | [Coin Metrics API v4](https://docs.coinmetrics.io/api/v4/) | not_a_profit_test |
| CM_COMMUNITY | [Coin Metrics Community Data](https://docs.coinmetrics.io/packages/coin-metrics-community-data) | not_a_profit_test |
| CB_CANDLES | [Coinbase Exchange Get product candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles) | not_a_profit_test |
| CB_TERMS | [Coinbase Market Data Terms of Use](https://www.coinbase.com/legal/market_data) | not_a_profit_test |
| BINANCE | [Binance Public Data](https://github.com/binance/binance-public-data) | not_a_profit_test |
| KRAKEN | [Kraken downloadable historical OHLCVT data](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data) | not_a_profit_test |
| KRAKEN_API | [Kraken Get OHLC Data](https://docs.kraken.com/api-reference/market-data/get-ohlc-data) | not_a_profit_test |
| DERIBIT | [Deribit API documentation](https://docs.deribit.com/) | not_a_profit_test |
| DERIBIT_FUNDING | [Deribit public/get_funding_rate_history](https://docs.deribit.com/api-reference/market-data/public-get_funding_rate_history) | not_a_profit_test |
| FNG | [Alternative.me Fear and Greed Index and API](https://alternative.me/crypto/fear-and-greed-index/#api) | not_a_profit_test |
| GDELT | [GDELT data access](https://www.gdeltproject.org/data.html) | not_a_profit_test |
| GDELT_DOC | [GDELT DOC 2.0 API Debuts](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) | not_a_profit_test |
| FRED | [FRED API](https://fred.stlouisfed.org/docs/api/fred/) | not_a_profit_test |
| ALFRED | [FRED real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html) | not_a_profit_test |
| SEC | [SEC developer resources](https://www.sec.gov/about/developer-resources) | not_a_profit_test |
| LLAMA | [DeFiLlama API](https://defillama.com/docs/api) | not_a_profit_test |
| TOKENOMIST | [Tokenomist API introduction](https://docs.tokenomist.ai/api-documents/introduction) | not_a_profit_test |
| GECKO | [CoinGecko API](https://docs.coingecko.com/) | not_a_profit_test |
| TAXONOMY | [151 Trading Strategies](https://doi.org/10.1007/978-3-030-02792-6) | not_evidence |
| NVT | [Glassnode NVT methodology](https://docs.glassnode.com/further-information/metric-guides/nvt/nvt-ratio) | metric_definition_not_profit |
| MVRV | [Glassnode MVRV methodology](https://docs.glassnode.com/further-information/metric-guides/mvrv/mvrv-ratio) | metric_definition_not_profit |
| SOPR | [Glassnode SOPR methodology](https://docs.glassnode.com/further-information/metric-guides/sopr/sopr-spent-output-profit-ratio) | metric_definition_not_profit |
| PERIODICITY | [Periodicity in Cryptocurrency Volatility and Liquidity](https://arxiv.org/abs/2109.12142) | descriptive_not_strategy_validation |
