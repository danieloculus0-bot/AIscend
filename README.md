# AIscend 🌱
### Autonomous Capital Lab, powered by BEAN learning

**An open-source, small-bankroll autonomous trading experiment with real execution adapters, persistent feedback learning, and an evidence-first research lab.**

AIscend asks a simple question: **what can an adaptive agent do with a tiny, finite bankroll when it is free to make BUY, SELL, and HOLD decisions, study its mistakes, and change its behavior?** The experiment was designed around roughly **$25** of starting capital. The target for **Level 1 is $100,000 in net liquidation value**, not a claimed result.

AIscend is the product name. `AutonomousCapitalLab` remains the default desktop executable and build identity. Its companion is [BEAN Core](https://github.com/danieloculus0-bot/BEAN), but AIscend's embedded learning system and the separate BEAN Core repository are **not the same runtime**.

[**Explore the public AIscend interface demo**](https://danieloculus0-bot.github.io/BEAN/aiscend.html) · [**BEAN project**](https://danieloculus0-bot.github.io/BEAN/) · [**Source**](https://github.com/danieloculus0-bot/AIscend) · [**Windows builds**](https://github.com/danieloculus0-bot/AIscend/actions/workflows/windows-build.yml)

> **Demo versus actual software:** The public web demo is a clearly separated, sample-data presentation. It is not the running trader, does not connect to a funded wallet, and cannot establish trading performance. The executable and Python/Flask application in this repository contain the actual logic. No made-up paper-buy terminal or invented exchange modules are part of the application.

## At a glance

| | Current implementation |
|---|---|
| **Source version** | `0.17.4` (from [VERSION](VERSION)) |
| **Desktop** | Windows, PySide6; persistent offline-capable synthetic market |
| **Browser** | Flask dashboard, local or in GitHub Codespaces |
| **Funded execution rails** | Coinbase CDP smart account on Base; Coinbase Advanced spot via separate credentials |
| **Portfolio** | Linked Base + Advanced net value, decisions, optional explicit USDC bridge |
| **Live learning** | BEAN's persistent five-minute price-bar reward learner can change the next live BUY/SELL/HOLD decision |
| **Cross-market rotation** | Advanced spot can rank learned candidates, sell a weaker holding, and buy a stronger one on a later cycle |
| **Independent research** | Historical Strategy School, forward paper validation, universe scans, listing research, separate BEAN Core advisory bridge |
| **Release pipeline** | Windows Python 3.12 tests, PyInstaller EXE, ZIP and SHA-256 verification |
| **Verified CI** | [Main build at `d7fc8e6`](https://github.com/danieloculus0-bot/AIscend/actions/runs/38232312735) completed successfully on October 12, 2026. This verifies source/build checks, **not** live profitability, real fills, or a locally installed version. |

### What makes AIscend different

- **It can act:** its Base and Coinbase Advanced engines have concrete order/submission paths, rather than a decorative trading interface.
- **It can change its behavior:** a contextual, Q-like reward table learns from chronologically completed five-minute Coinbase candles. A reward-driven preference change can influence actual orders through the existing adapters.
- **It can compare assets:** the Advanced engine ranks eligible Coinbase markets using learned reward, observed spread and volume, and can exit an inferior position before attempting a new entry.
- **It remembers:** local SQLite stores bankroll and decisions, learned market states, experimental candidates, forward-paper outcomes, and durable research lessons.
- **It has separate evidence tracks:** simulated rewards, historical backtests, mocked execution tests, accepted exchange orders and verified actual returns are **different things**. The dashboard and documentation should never blur them.

## Choose your mode

| Mode | What it does | Requires credentials? |
|---|---|---|
| **Synthetic desktop** | Persistent synthetic prices, autonomous decisions, game score, ledger and journal | No |
| **Base Sepolia** | CDP smart-wallet path with testnet assets | CDP |
| **Base mainnet** | CDP wallet, USDC/WETH pricing and Base onchain swaps | CDP and funded wallet |
| **Coinbase Advanced** | Eligible spot product discovery, market BUY/SELL, holdings and journaling | Advanced API key and eligible account |
| **Linked live portfolio** | Monitors Base and Advanced as one capital experiment; chooses an eligible funded rail each cycle | Appropriate rail access |
| **Strategy School / forward paper** | Historical or next-bar experiments, independent of actual orders | Public price data, not trading keys |

The Market Arsenal also shows **Predictions, Stocks & ETFs, Futures, Perpetuals, and Options** as research/product lanes. These are **not** six live trading connectors. Prediction-market discovery is implemented using public data, but authenticated prediction order execution is not connected. The remaining non-crypto lanes do not have live execution adapters.

## Version 0.17.4: cost-aware rotation and maturity research

AIscend's Coinbase Advanced Spot path now evaluates whether freeing capital
from a held asset and entering another has a positive **modeled net outcome**
after estimated current-asset selling costs, new position entry and exit fees,
spread, and any required USD/USDC spot conversion. Coinbase account taker-fee
data is used when available; an explicit 60-basis-point per-leg estimate is
used when the rate cannot be retrieved. The actual order preview and exchange
minimums are still validated before order submission.

The candidate's estimated growth is extrapolated from hypothetical BEAN
five-minute-bar rewards into a provisional 5-, 15-, 60-, or 360-minute
maturity period, using observed momentum persistence. **This extrapolation is
not a validated probability or guaranteed forecast.** BEAN also records the
empirical frequency of positive simulated rewards after this release, marking
outcomes from older untracked history as unavailable.

When estimated returns exceed every required route cost, the live loop can
SELL a weaker held coin, then perform a supported USD/USDC spot conversion,
then BUY the replacement, on separate confirmed cycles. It journals the
route and any failures. A stored maturity deadline supports re-evaluation and
exit rather than indefinite HOLD. Coinbase product tradability, account
balances, increments, minimums, and authenticated order previews remain
required. This does not create leveraged shorts or manipulate market prices.

If no candidate clears estimated route costs, HOLD remains possible and the
journal explains why; it is not evidence that better opportunities do not
exist.

## October 2026 trading and market-coverage correction

- v0.17.4 also removes the stale entry and exit thresholds in the browser dashboard's `auto` execution-rail selector. Selecting `auto` no longer imposes a stricter gate than the actual trading engine.


- The public Coinbase scanner now samples 24 products in a concurrent batch per scan, retaining previously sampled rows long enough to grow coverage across the catalog. It does not refresh every product at once.
- The live BEAN evaluator checks 48 observed candidates per cycle, combining highly-ranked opportunities with a persistent rolling cursor across the remainder. Held positions are always eligible for evaluation.
- Advanced BUY decisions can request 100% of spendable quote cash. Market increments, available balances and exchange acceptance still govern what actually fills.
- Learned switching compares modeled relative outcomes, without an additional fixed 18-basis-point hurdle. Scanner-based entry and exit no longer use the former fixed score/spread/volume cutoffs.
- Strategy School records evidence but does not independently veto an otherwise valid BUY. BEAN's adopted learned cash/long target can still legitimately decide to HOLD.
- This correction does **not** establish realized trading gains; confirm live portfolio outcomes against Coinbase order history and the audit journal. The web dashboard is started with `python web_app.py`, not `python main.py` (the desktop application).

## BEAN learning: what is genuinely wired into live decisions

The newest material changes are in [`src/bean_growth_adaptive.py`](src/bean_growth_adaptive.py), [`src/bean_growth_rotation.py`](src/bean_growth_rotation.py), [`src/live.py`](src/live.py) and [`src/advanced_live.py`](src/advanced_live.py).

### 1. Learn from completed five-minute market bars

BEAN reads *completed* public Coinbase candles and tracks distinct per-market state in local SQLite, including:

- Five strategy actions: `momentum`, `reversion`, `trend_3`, `buy_hold` and `cash`.
- Price-return context and a long-versus-cash target.
- Next-bar hypothetical position rewards, including a modeled transaction cost.
- Context/action reward values, action visits, actual preference changes and forward observation counts.
- Persistent replay of its own state after restarting the application.

A negative reward can change the preferred next action. Regression tests verify this behavioral effect separately from simply incrementing a learning counter. This is a **small contextual reward learner**, not a neural DeepMind Atari system, not BEAN Core's separate neuroevolution research, and not proof of profitable generalization.

### 2. Adopt the learned policy in the trading engines

When the learner has enough valid data, its long/cash target can become a **real decision input**, not merely a shadow recommendation:

- **Base** can convert the learned ETH exposure into a USDC/WETH BUY, SELL or HOLD through its existing CDP wallet path.
- **Coinbase Advanced** can assess eligible products and use the learned outcome for its real spot execution decision.
- Existing account and venue constraints still apply. Missing candles, unavailable products, rejected orders and missing spendable quote balances must not be fabricated into fills or positive rewards.
- The [hourly Strategy School](src/bean_policy.py) is a **separate advisory research track**. Its historical selection does not veto an otherwise executable market BUY.

**Crucial distinction:** tests using fake wallets demonstrate that a changed learned preference reaches the proper order methods. They do **not** prove that the user's connected exchange filled those orders, or that future returns improved.

### 3. Rank, exit and rotate capital on Advanced spot

The [cross-market rotation module](src/bean_growth_rotation.py) examines eligible scanner candidates and already-held positions, even when a held asset is missing from the current top scanner results. It ranks adopted long strategies by learned reward adjusted for modeled spread and cost.

The [Advanced live engine](src/advanced_live.py) can sell a held asset when its learned policy favors cash or another opportunity clears the coded improvement threshold. Proceeds can support a different BUY on a **subsequent** cycle. The sell and subsequent buy are **not one atomic exchange transaction**.

The existing [rotation tests](tests/test_bean_growth_rotation.py) verify mocked SELL then BUY execution calls and durable decision records. The ranking formula and threshold are research heuristics, **not** independently validated trading edge or a prediction of actual net returns.

### 4. Keep research, advisory reasoning and live execution separate

| Component | Evidence / action boundary |
|---|---|
| [Hourly Strategy School](src/bean_strategy_lab.py) | Researches seven causal rule families on Coinbase history; stores selected and rejected candidates and chronological holdout results |
| [Unattended research supervisor](src/bean_research_supervisor.py) | Checks completed hourly windows after a settling delay, runs at most one new experiment per fresh window, persists a restart-safe cursor; runs while the local Flask process is alive |
| [Five-minute research and forward-paper book](src/bean_five_minute.py) | Tests new five-minute candidates and next-bar hypothetical trades without accessing wallet/order methods |
| [Embedded BEAN memory](src/bean.py) | Tracks typed claims, contradictory evidence, market observations, predictions and graded signal/source trust |
| [BEAN Core advisory adapter](src/bean_core_bridge.py) | Can call a separately checked-out BEAN Core mock reasoning adapter using a credential-free observation; cannot authorize an exchange trade |
| [Local Ollama engineering loop](docs/BEAN_UNATTENDED_LOCAL_ENGINEER.md) | Optional explicit PowerShell launch; proposes and tests local code edits, and **can modify/push source** if allowed by the user's checkout. It is not a cloud daemon that operates when the PC is off. |

`AUTO_SELECTED` in Strategy School means a **research candidate passed its historical selection check**. The newer five-minute live-adoption path is separate. Neither label means realized wallet profit.

## The product interface

The browser dashboard and desktop app expose execution controls, linked capital, net liquidation, positions and the **Decision Journal**. The interface also includes:

- **BEAN Strategy School** with research history and experiment status.
- **Adaptive growth / learning evidence** including reward observations and actual policy preference revisions.
- **Market Arsenal** with clear research-only versus executable lane labels.
- **Coinbase asset universe and Listing Sentinel**, which scan public metadata and track newly visible products and trading-state changes.
- **Human Weather and the FOMO Index**, combining market momentum with public news, social sentiment, politics, unusual events, Fear & Greed and feed-quality information.
- **An intentionally retro BEAN trader pet** that responds visually to trading or research state, without changing order logic.

The dashboard uses the project's **BEAN dark-Mint design language** and official sprouted BEAN identity. The public [interface demo](https://danieloculus0-bot.github.io/BEAN/aiscend.html) is a separate illustrative surface; this repository's [`templates/index.html`](templates/index.html) is the Python application's own dashboard.

## Game rules

The challenge is maximum bankroll growth with speed measured independently:

```text
score = floor(log2(current net liquidation / original bankroll))
```

- Every **completed bankroll doubling** earns one point. Trading frequency alone earns nothing.
- The baseline bankroll stays tied to the original experiment when funds move between Base and Advanced. Moving funds is **not** profit.
- **Level 1** requires **$100,000** of verified net liquidation value. With a $25 start, that is a 4,000x target, slightly less than twelve complete doublings.
- HOLD is a legitimate decision. No borrowing, margin or short sales are implemented in the current spot strategy.
- A displayed score is only as reliable as its holdings valuation. Unpriced assets, pending orders, fees and transfer reconciliation can make a provisional net value incomplete.

Nothing in this repository documents a completed Level 1 victory.

## Install and run on Windows

Python **3.11+** is required. GitHub CI currently uses **Python 3.12**.

```powershell
git clone https://github.com/danieloculus0-bot/AIscend.git
cd AIscend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Desktop, synthetic by default
.\.venv\Scripts\python.exe main.py

# Or start the local browser dashboard
.\.venv\Scripts\python.exe web_app.py
```

The browser dashboard is served at [http://127.0.0.1:8000](http://127.0.0.1:8000). Starting the browser app also enables its local research supervisor. Closing the Python server stops that supervisor.

### Alternative: GitHub Codespaces

Choose **Code > Codespaces > Create codespace on main** in this repository. [`devcontainer.json`](.devcontainer/devcontainer.json) installs dependencies and starts `web_app.py` with port **8000** forwarded.

Codespaces is a remote hosted environment, not your already-funded local Windows installation. It does not inherit Windows Credential Manager entries or automatically carry over local SQLite research and trade history. Treat live keys in any hosted environment as sensitive credentials.

### Connect Coinbase deliberately

**Base CDP smart wallet:** Use the Windows app's **CONFIGURE COINBASE** action or the local configuration script:

```powershell
.\configure-cdp.ps1 -KeyFile "C:\path\to\cdp_api_key.json"
.\.venv\Scripts\python.exe scripts\cdp_wallet.py --network base status
```

The script prompts for the wallet secret; it should not be pasted into GitHub, issues, commit messages, website demos or chat messages. On Windows, supported credentials are persisted through the operating-system credential backend. CDP and Advanced Trade use **separate credentials and execution rails**.

**Coinbase Advanced:** Connect through the browser application's Advanced configuration controls with an appropriate Advanced Trade key. Eligibility, available quote balances and the provider's order acceptance rules apply.

The default local synthetic experience needs **no exchange account, paid API or funded wallet**.

## Building, testing and deployment

```powershell
# Complete Python regression suite
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Focused live-learning and rotation tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_bean_growth_adaptive.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_bean_growth_live_adoption.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_bean_growth_rotation.py -v

# Canonical Windows build
.\build.ps1
```

`build.ps1` runs regression tests, compiles Python sources, builds a one-file PyInstaller EXE, packages a versioned Windows ZIP and generates a SHA-256 checksum.

```text
dist\AutonomousCapitalLab.exe
dist\AutonomousCapitalLab-v0.17.4-windows-x64.zip
dist\AutonomousCapitalLab-v0.17.4-windows-x64.zip.sha256
```

The [Windows Build workflow](.github/workflows/windows-build.yml) runs on pushes and pull requests to `main`, relevant version tags, and manual dispatch. The [Adaptive Growth mock integration workflow](.github/workflows/bean-adaptive-growth-smoke.yml) exercises focused learned-action and rotation behavior on **Windows and Ubuntu**.

**A green test is not a deployed desktop update.** Pull the new commit and restart/rebuild the local installation. A successfully submitted Coinbase order is not automatically a verified fill.

## Persistent data, wallet boundaries and remote monitor

- **Synthetic state:** `%LOCALAPPDATA%\AutonomousCapitalLab\state.db` on Windows.
- **Live state:** separate SQLite rail journals and a linked portfolio view; the original game baseline carries across rails.
- **Learning state:** persistent five-minute learner SQLite history and separate Strategy School / forward-paper research records in the application data directory.
- **Trade audit:** yearly append-only JSONL and CSV containing trade versus bridge-transfer classifications, timestamps and available provider IDs. Sensitive provider responses are redacted where implemented.
- **Credential store:** Windows Credential Manager/keyring for locally configured exchange keys. No secrets belong in source files or a demo.
- **Private remote monitoring:** `/monitor` is read-only. [`scripts/start_remote_monitor.ps1`](scripts/start_remote_monitor.ps1) requires Tailscale and an `AISCEND_REMOTE_TOKEN`, and trading endpoints remain restricted to local access.

A remote monitor is not a remote autonomous runner. A PC-off Python process is not training or trading in the background.

## Evidence and limitations

The repository has **real executable trading integrations and real causal reward updates**. The following claims are **not established** by code tests alone:

1. That the learned strategy outperforms an uninterrupted fixed strategy after **actual spreads, network fees, partial fills and market impact**.
2. That every submitted Advanced order has a confirmed fill and fee reconciliation, or that every held coin always has reliable USD liquidation pricing.
3. That historical Strategy School selection, hypothetical paper-forward equity or mock-adapter orders equal wallet returns.
4. That experimental BEAN Core neural-learning results have been independently replicated inside AIscend's live policy.
5. That prediction, equities, futures, perpetuals or options markets can currently be traded through working adapters.

**Next engineering priorities:** reconcile Advanced order statuses/fills and actual fees end to end; establish trustworthy account-wide valuation before awarding milestones; independently compare the live-adopted reward policy to frozen controls on fresh forward periods; evaluate capital rotation after realized friction; and preserve all positive *and* negative experiments.

## Project map

| Start here | Source |
|---|---|
| Desktop entry point | [`main.py`](main.py), [`src/ui.py`](src/ui.py) |
| Browser entry point and linked runner | [`web_app.py`](web_app.py), [`templates/index.html`](templates/index.html) |
| Base smart-wallet trading | [`src/live.py`](src/live.py), [`src/wallets/`](src/wallets/) |
| Coinbase Advanced spot | [`src/advanced_live.py`](src/advanced_live.py) |
| Reward learning and rotation | [`src/bean_growth_adaptive.py`](src/bean_growth_adaptive.py), [`src/bean_growth_rotation.py`](src/bean_growth_rotation.py) |
| Historical Strategy School | [`src/bean_strategy_lab.py`](src/bean_strategy_lab.py) |
| Five-minute forward-paper research | [`src/bean_five_minute.py`](src/bean_five_minute.py) |
| Research supervisor | [`src/bean_research_supervisor.py`](src/bean_research_supervisor.py) |
| BEAN Core adapter | [`src/bean_core_bridge.py`](src/bean_core_bridge.py) |
| Full architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Local engineering automation | [`docs/BEAN_UNATTENDED_LOCAL_ENGINEER.md`](docs/BEAN_UNATTENDED_LOCAL_ENGINEER.md) |
| Repository tests | [`tests/`](tests/) |

**Source snapshot:** README revised October 12, 2026 against `d7fc8e65c4fe9db5a1038459a0d82738fda92f7b`. Future commits, exchange availability and local installations may differ. Git history retains the original extended version-by-version notes.
