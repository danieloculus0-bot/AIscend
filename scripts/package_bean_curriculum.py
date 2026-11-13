"""Package completed training, signed outcomes and plain-language strategy lessons."""
from __future__ import annotations
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import numpy as np
from src.bean_strategy_curriculum import ACTIONS

RULES={
 'cash':'Sell to cash; useful when expected gains fail to cover trading costs or risk.',
 'hold':'Keep existing units; avoids paying for an unnecessary rebalance.',
 'buy_hold':'Target full funded exposure, with no leverage.',
 'sma_trend':'Target exposure when trailing 50-day mean exceeds 200-day mean.',
 'price_trend':'Target exposure when the known close exceeds its trailing 200-day mean.',
 'donchian_breakout':'Enter above the prior 20-day high; retain until below the prior 10-day low.',
 'rsi_pullback':'Enter an oversold pullback within an uptrend; exit on RSI recovery or trend failure.',
 'bollinger_revert':'Enter below two trailing standard deviations; retain until the trailing mean recovers.',
 'bullish_engulf':'Enter after a bullish real body engulfs the prior bearish body, above the 50-day mean.',
 'hammer_rebound':'Enter after a green candle with a long lower wick during a trailing seven-day decline.',
 'risk_scaled_trend':'Reduce funded exposure as trailing volatility increases, inside the price trend.',
}

def package(run,source,output):
    run,source,output=map(Path,(run,source,output));output.mkdir(exist_ok=True,parents=True)
    for p in run.iterdir():
        if p.name.endswith(('.sqlite3','.jsonl')):continue
        shutil.copy2(p,output/p.name)
    target=output/'source';target.mkdir(exist_ok=True)
    for p in source.iterdir():shutil.copy2(p,target/p.name)
    report=json.loads((output/'results.json').read_text());protocol=json.loads((output/'protocol.json').read_text())
    buckets=defaultdict(list);signed={'positive':0,'negative':0,'tie':0};dates=[]
    with sqlite3.connect(run/'teaching_memory.sqlite3') as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Teaching memory failed integrity check')
        for symbol,t,action,reward,exposure,raw,evidence in con.execute('SELECT symbol,outcome_at,action,reward,prior_exposure,features_json,evidence_json FROM experiences'):
            signed['positive' if reward>1e-12 else 'negative' if reward< -1e-12 else 'tie']+=1;dates.append(t)
            if exposure!=0:continue
            v=json.loads(raw);trend='above_200day_mean' if v[8]>0 else 'below_200day_mean';vol='high_volatility' if v[9]>1 else 'lower_volatility';rsi='oversold' if v[10]<-.4 else 'overbought' if v[10]>.4 else 'middle_rsi'
            e=json.loads(evidence);buckets[(action,trend,vol,rsi)].append((e['net_return'],e['costs'],symbol,t))
    lessons=[]
    for (action,trend,vol,rsi),items in sorted(buckets.items()):
        if len(items)<12:continue
        returns=np.array([x[0] for x in items]);lesson={'action':action,'mechanism':RULES[action],'context':{'trend':trend,'volatility':vol,'rsi':rsi},'current_exposure':0,'examples':len(items),'symbols':sorted({x[2] for x in items}),'mean_net_period_return':float(returns.mean()),'median_net_period_return':float(np.median(returns)),'fraction_positive':float(np.mean(returns>0)),'worst_period_return':float(returns.min()),'mean_paid_cost':float(np.mean([x[1] for x in items])),'latest_outcome_at':max(x[3] for x in items),'interpretation':'Descriptive training evidence with correlated periods; not a trading recommendation or an independent profitability test'};lessons.append(lesson)
    (output/'strategy_lessons.json').write_text(json.dumps({'schema':'aiscend.bean.strategy_lessons.v1','rules':RULES,'signed_teaching_outcomes':signed,'lessons':lessons,'knowledge_cutoff':protocol['development_audit'][0],'retention':'All underlying positive, negative and tied rows remain in teaching_memory.sqlite3.gz; lesson summaries do not replace them'},indent=2)+'\n')
    import pandas,torch
    environment={'python':sys.version,'platform':platform.platform(),'numpy':np.__version__,'pandas':pandas.__version__,'torch':torch.__version__,'core_revision':protocol['core_revision']}
    (output/'environment.json').write_text(json.dumps(environment,indent=2)+'\n')
    rows=[]
    for trial in report['trials']:rows.append(f"| {trial['seed']} | {trial['budget']} | {trial['fold_returns'][0]:+.2%} | {trial['fold_returns'][1]:+.2%} |")
    audit=report['selected_development_audit'];comparison=[]
    for name,result in [('trained strategy + pattern memory',audit)]+list(report['baselines'].items()):comparison.append(f"| {name} | {result['equal_weight_mean_return']:+.2%} | ${1000*(1+result['equal_weight_mean_return']):,.2f} | {result['worst_asset_drawdown']:.2%} |")
    text=f'''# BEAN strategy curriculum, candle transfer and retained state

The request was to teach strategies, apply their lessons to similar candles and keep learned state. This study does those things using the actual BEAN Core replay learner. The trained policy averaged **{audit['equal_weight_mean_return']:+.2%}** in the later development period, but lost in both earlier evaluation folds and underperformed simpler controls. **Development qualification: {'PASSED, STILL REQUIRES FRESH EVALUATION' if report['development_qualification_passed'] else 'FAILED'}.** A robust learned advantage was not established. These historical periods overlap evidence seen in prior research, so none is described as a new unseen profitability test.

## What BEAN learned from

- 14,608 actual OHLCV candles across eight fixed spot symbols, October 2020 through September 2025. All 480 official Binance monthly archives passed their published SHA-256 checksums. Raw archives and receipts are retained in `source/`.
- {report['teaching_examples']:,} fee-inclusive counterfactual teaching examples, covering all 11 rule actions and cash/half/full starting exposure. There are {signed['positive']:,} positive, {signed['negative']:,} negative and {signed['tie']:,} tied rewards. They reuse correlated market periods and are not that many independent observations.
- Shared, dimensionless 47-value inputs describe trailing returns, trend, volatility, RSI, volume and five actual candle bodies/wicks. No symbol or date identity or future-fitted scaler is an input.
- Fixed strategy rules teach trend, breakout, pullback, reversion, engulfing, hammer, risk scaling, cash and hold. These are explicit example algorithms; candlestick names alone are not evidence of predictive power.
- Neural strategy values are combined with 32 nearest previously matured patterns on other symbols, subject to a fixed support/distance rule. `strategy_lessons.json` provides conditional human-readable evidence. The full signed case memory remains in `teaching_memory.sqlite3.gz`.

The learner is BEAN `SpongeDQN` at `{protocol['core_revision']}`, using replay, Adam and Huber updates with gamma=0 for eight-day strategy-value regression. It is not a new general-purpose model. The policy process receives vectors and mature labels, not historical price files. This experiment uses a standalone research adapter; the hosted BEAN notes connector and live application are not automatically upgraded.

## Repeated training and development results

Two seeds and three update budgets were compared on two chronological forward development folds. All training labels matured before each fold's decision time. The rule selected the configuration with the best worst-fold result, locked before the later development audit. The 2025-10-24 through 2026-10-23 final year from the original experiment was excluded. More gradient steps are not more independent market evidence.

| Seed | Gradient updates | Apr-Sep 2023 | Apr-Sep 2024 |
| --- | ---: | ---: | ---: |
{chr(10).join(rows)}

Selected seed {report['selected']['selected_seed']}, budget {report['selected']['selected_budget']}. Trained through outcomes available by 2025-04-01, then frozen for April-September 2025 development diagnostics. Each symbol has a separately funded $1,000 account. Returns below are equal-weight means across those eight accounts, not a single account or an independently tested cross-asset portfolio.

| Development audit arm | Mean net return | Mean ending account | Worst asset daily-close drawdown |
| --- | ---: | ---: | ---: |
{chr(10).join(comparison)}

The untrained architecture retains the same pattern-memory component, isolating neural fitting within the hybrid. The pattern-only control omits the network. All arms use the same dates, symbols, delayed reference-open fills and 1% modeled per-side costs. Drawdown uses daily closes, not intraday executable prices. These examples do not establish current venue fees or tradability.

## Retention and reuse

All twelve intermediate fold checkpoints remain inside four SHA-linked Core journals. The selected final policy retains its weights, target, optimizer, RNG and 4,096 replay cases. Actual selected updates: {report['actual_neural_updates_selected']}; restart reproduced both learned state and diagnostic values: {report['restart_verified']}. The exact journal file/state hashes and knowledge cutoffs are in `retention_manifest.json`. The final decision evidence contains {report['decision_records']:,} SHA-linked records.

```bash
python -m scripts.bean_strategy_policy --directory research/bean_strategy_curriculum_20261024 --core-dir ../BEAN --as-of 2026-10-25T00:00:00Z
```

An earlier checkpoint is recoverable using `--policy-id fold-0-seed-7-updates-256`; it is recovered by its exact weight hash from the journal rather than replaced by the latest checkpoint. Future learned state is rejected when a requested date predates its cutoff. The original numeric-boundary aborted attempt and its partial losses are preserved in `research/bean_curriculum_aborted_numeric_20261024.zip`.

Continue development using the same explicit teaching/validation procedure, keeping all trials. The current command has bounded budgets so it cannot silently search forever and report only a lucky winner. A revised model needs a separately registered evaluation on genuinely new forward or independent evidence before any claim of useful trading learning. No live orders were sent.

## Limits and provenance

The eight instruments were selected in the present, excluding many failed/delisted instruments. This introduces survivorship, venue and correlated-asset bias. Prices are USDT-denominated archive candles, not USD quotes or orderbook fills. Historical revisions, execution latency, spread/impact, availability and actual fees need venue-specific replication. Repeated known histories cannot prove future profit. No historical first-seen narrative dataset was supplied, and current headlines were not backdated.

Official source documentation: https://github.com/binance/binance-public-data . Source terms: https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md . Original failed portfolio experiment: https://github.com/danieloculus0-bot/AIscend/pull/86 . Core implementation: https://github.com/danieloculus0-bot/BEAN/pull/166 .

READY FOR REVIEW: separate experiment branch; reference-price research only; ten meaningful contract tests passed locally with actual Core gradients and restart. No profitable production behavior is asserted.
'''
    (output/'RESULTS.md').write_text(text)
    files={str(p.relative_to(output)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(output.rglob('*')) if p.is_file() and p.name!='artifacts_manifest.json'}
    (output/'artifacts_manifest.json').write_text(json.dumps({'schema':'aiscend.bean.curriculum_artifacts.v1','files':files},indent=2)+'\n')
    print(json.dumps({'teaching_outcome_signs':signed,'conditional_lessons':len(lessons),'files':len(files),'result':report['development_qualification_passed']}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();package(a.run,a.source,a.output)
