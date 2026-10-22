# BEAN Core learning in AIscend's Python environment

This is **real BEAN Core SpongeTD**, not the similarly named AIscend
`BeanAdaptiveGrowthGate`. The code uses BEAN's separately checked-out
`bean.learning.runtime.LearningRuntime` inside a credential-free Python
subprocess. Neither AIscend nor BEAN Core can issue a Coinbase order through
this interface.

## Learning loop actually connected

1. Freeze a Coinbase product/strategy manifest **before** seeing new candles.
   The full-universe historical research artifact may offer training-ranked
   candidates. If none qualify, there is no honest winner to promote.
2. At each observation, read completed public Coinbase 5-minute candles and
   persist the signal time. The immediately open candle has **already** opened:
   the earliest hypothetical BUY is the following *future* candle's OPEN.
3. Before that OPEN, BEAN reads its Q-state and commits a prospective
   `trade` or `cash` shadow choice with an observation timestamp.
4. After the hypothetical exit candle completes, the local paper engine
   calculates the actual paper return **after modeled buy/sell fees and spread**.
   Incomplete candles are rejected as data gaps; paper results are never
   Coinbase fills.
5. BEAN replays chronological, checked paper receipts using its actual
   LearningRuntime.choose/feedback, credits any precommitted shadow action,
   updates SpongeTD Q-values and creates score-attested SHA-linked checkpoints
   after at least 30 scored observations since the prior checkpoint.
6. On restart BEAN verifies its previous checkpoint's receipt-prefix digest;
   changed or shortened historical evidence is rejected. Recent updates that
   have not reached checkpoint quorum are replayed, not represented as durable.

The initial retrospective replay of older paper trades is **learning data,
not a forward performance claim**. Only choices committed before future paper
entries can be counted toward prospective shadow-policy evaluation. The bridge
never auto-promotes strategies, assumes account profits, or enables funded
trading. Its quality checkpoint uses a neutral 0-versus-0 host score and is
deliberately **not** an improvement certification.

## Run on the existing Python checkout

Check out the real [BEAN Core](https://github.com/danieloculus0-bot/BEAN)
as a sibling folder to AIscend (for example, `../BEAN`). Use the Python
environment in which the AIscend source dependencies already work.

First freeze qualified training-only candidates from the full Coinbase
research aggregate (if there are none, this command correctly refuses):

```bash
python -m scripts.forward_paper --freeze-aggregate universe-aggregate.json --output frozen-candidates.json
```

Alternatively, precommit your own **research-only** manifest *before*
watching outcomes. Example, not a market recommendation:

```json
[
  {"product": "BTC-USD", "policy": "trend", "training_only_frozen": true},
  {"product": "ETH-USDC", "policy": "reversal", "training_only_frozen": true}
]
```

Run one scored observation:

```bash
python -m scripts.forward_paper --manifest frozen-candidates.json --bean-core ../BEAN
```

Or keep the actual Python paper/BEAN learning observer running in its own
foreground terminal (Ctrl+C stops it):

```bash
python -m scripts.forward_paper --manifest frozen-candidates.json --bean-core ../BEAN --watch --interval-seconds 300
```

The local SQLite files default to
`<user app-data>/AutonomousCapitalLab/forward-paper.sqlite3` and
`bean-core-paper.sqlite3`. Keep both databases together and backed up;
do **not** point them at the live Coinbase execution/order-journal database.
Existing paper ledgers using the invalid already-open-bar entry model
are rejected, not silently migrated to apparently profitable results.

## Verification and reality checks

`python -m scripts.bean_core_paper_smoke --core ../BEAN` runs the actual
BEAN runtime against **30 completely synthetic fixture outcomes**. It tests
a real Q update, compressed durable checkpoint, restart, an additional
precommitted future shadow choice, outcome feedback and evidence tampering
rejection. These fixtures are *not* the owner's Coinbase trades or live
market records.

The verified full-universe research run checked 389 public USD/USDC
markets, with 364 OOS_NOT_PROVEN and 25 insufficient-history results.
There is no demonstrated profitable strategy and no authenticated Coinbase
verification on the owner's installed Python desktop. The existing live
AIscend trader does **not** delegate order authority to this BEAN Core
research learner. Promote nothing until independent forward evidence and
real-account cash-flow/fill reconciliation meet explicit release criteria.
