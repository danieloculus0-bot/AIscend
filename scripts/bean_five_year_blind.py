"""Five-year BLIND, chronological public Coinbase data evaluation of REAL BEAN Core.

Host (evaluator) alone reads the daily candle CSV; a credential-free BEAN
subprocess receives ONLY past-derived observations and delayed realized rewards.
The model cannot read the CSV, upcoming candles, or Coinbase API credentials.
There is no parameter search, no tuning on outcomes, no live order authority.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from src.bean_core_bridge import core_identity

START = date(2021, 10, 22)
END_EXCLUSIVE = date(2026, 10, 22)
PRODUCTS = ("BTC-USD", "ETH-USD")
DATA = Path("data/market/coinbase_btc_eth_daily_2021-10-22_2026-10-21.csv")
MANIFEST = DATA.with_suffix(".manifest.json")
FEE_BPS = 60.0
SPREAD_BPS = 10.0
LOOKBACK = 84
STEP = 7
HOLD = 7
SCHEMA = "aiscend.bean.five-year-blind.v1"
FIELDS = ("product", "date", "open", "high", "low", "close", "volume")


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request_candles(product, start, end):
    params = urlencode({
        "start": start.isoformat() + "T00:00:00Z",
        "end": end.isoformat() + "T00:00:00Z",
        "granularity": 86400,
    })
    url = ("https://api.exchange.coinbase.com/products/"
           + product + "/candles?" + params)
    for attempt in range(7):
        try:
            req = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "AIscend-research-blind-backtest/1.0"})
            with urlopen(req, timeout=35) as reply:
                rows = json.loads(reply.read(2_000_000))
            if not isinstance(rows, list):
                raise ValueError("Coinbase returned a non-candle response")
            return rows
        except (HTTPError, URLError, TimeoutError) as err:
            if isinstance(err, HTTPError) and err.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 6:
                raise
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError("Coinbase retry exhaustion")


def acquire(path=DATA):
    """Fetch 5 years in <=180-day public-only Coinbase API windows."""
    path = Path(path)
    all_rows = []
    requests_made = 0
    for product in PRODUCTS:
        found = {}
        begin = START
        while begin < END_EXCLUSIVE:
            until = min(END_EXCLUSIVE, begin + timedelta(days=180))
            raw = request_candles(product, begin, until)
            requests_made += 1
            for record in raw:
                if not isinstance(record, list) or len(record) < 6:
                    raise ValueError("Bad Coinbase candle structure")
                stamp = datetime.fromtimestamp(int(record[0]), timezone.utc)
                day = stamp.date()
                if day < begin or day >= until or stamp.time().isoformat() != "00:00:00":
                    continue
                numbers = [float(record[i]) for i in (3, 2, 1, 4, 5)]
                opening, high, low, close, volume = numbers
                if (not all(math.isfinite(x) for x in numbers)
                        or min(opening, high, low, close) <= 0 or volume < 0
                        or high < max(opening, close)
                        or low > min(opening, close)):
                    raise ValueError("Invalid candle price, volume or range")
                values = [product, day.isoformat()] + [
                    format(v, ".12g") for v in numbers]
                if day in found and found[day] != values:
                    raise ValueError("Conflicting Coinbase candle data")
                found[day] = values
            begin = until
        dates = [START + timedelta(days=i) for i in
                 range((END_EXCLUSIVE - START).days)]
        missing = [x.isoformat() for x in dates if x not in found]
        if missing:
            raise ValueError("Historical coverage incomplete for " + product
                             + ": " + repr(missing[:10]))
        all_rows.extend(found[x] for x in dates)

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(FIELDS)] + [",".join(row) for row in all_rows]
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    path.write_bytes(payload)
    manifest = {
        "schema": SCHEMA + ".data.v1",
        "source": "Coinbase Exchange public /products/{product}/candles",
        "url_template": "https://api.exchange.coinbase.com/products/{product}/candles",
        "request_granularity_seconds": 86400,
        "range_start_inclusive": START.isoformat(),
        "range_end_exclusive": END_EXCLUSIVE.isoformat(),
        "products": list(PRODUCTS),
        "rows": len(all_rows),
        "requests_made": requests_made,
        "csv_sha256": digest(payload),
        "actual_coinbase_orders": 0,
        "cashflows_authenticated": False,
    }
    path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"download": str(path), **manifest}, sort_keys=True))
    return manifest


def load(path):
    path = Path(path)
    raw = path.read_bytes()
    meta = json.loads(path.with_suffix(".manifest.json").read_text())
    if digest(raw) != meta["csv_sha256"]:
        raise ValueError("Coinbase dataset SHA256 mismatch")
    data = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("Invalid CSV header")
        for row in reader:
            p, d = row["product"], date.fromisoformat(row["date"])
            if p not in PRODUCTS or not START <= d < END_EXCLUSIVE:
                raise ValueError("Unexpected product or date")
            rec = {k: float(row[k]) for k in FIELDS[2:]}
            if (not all(math.isfinite(v) for v in rec.values())
                    or min(rec[k] for k in ("open", "close", "high", "low")) <= 0
                    or rec["volume"] < 0
                    or rec["high"] < max(rec["open"], rec["close"])
                    or rec["low"] > min(rec["open"], rec["close"])):
                raise ValueError("Invalid historic price")
            if (p, d) in data:
                raise ValueError("Duplicate candle date")
            data[(p, d)] = rec
    count = (END_EXCLUSIVE - START).days
    if len(data) != count * len(PRODUCTS) or len(data) != meta["rows"]:
        raise ValueError("Incomplete 5-year dataset")
    for p in PRODUCTS:
        for i in range(count):
            if (p, START + timedelta(days=i)) not in data:
                raise ValueError("Missing daily price for " + p)
    return data, meta


def signal_state(data, product, decision_day):
    """Strict past-only data view: no iterator over later market dates."""
    def close(n):
        return data[(product, decision_day - timedelta(days=n))]["close"]
    seven = close(0) / close(7) - 1
    medium = close(0) / close(28) - 1
    long = close(0) / close(84) - 1
    daily = [math.log(close(n) / close(n+1)) for n in range(14)]
    volatility = statistics.pstdev(daily)
    def bucket(value, bound):
        return "down" if value < -bound else "up" if value > bound else "flat"
    return {
        "symbol": product,
        "momentum_7d": bucket(seven, .035),
        "momentum_28d": bucket(medium, .09),
        "momentum_84d": bucket(long, .18),
        "volatility_14d": "high" if volatility >= .045 else "normal",
    }


# The process has no dataset path, no exchange SDK or trading keys.
# It only sees each timestamped past-derived state, and realized feedback
# after the evaluator has advanced the historical clock to exit time.
_WORKER = r"""
import json,random,sys
from bean.learning.runtime import LearningRuntime
a=LearningRuntime(sys.argv[1],actions=("trade","cash"),seed=17)
b=LearningRuntime(sys.argv[2],actions=("trade","cash"),seed=17)
try:
    for line in sys.stdin:
        x=json.loads(line)
        cmd=x["cmd"]
        if cmd=="choose":
            state=x["state"]
            scoped=a.scoped("aiscend-five-year-blind",state)
            q_before=list(a.policy.values(scoped))
            # The actual SpongeTD policy selects; 10% epsilon exploration
            # ensures losing early trades cannot strand it in cash forever.
            chosen=a.policy.actions[a.policy.choose(scoped,explore=True)]
            episode=a.choose(episode_id=x["episode"],domain="aiscend-five-year-blind",
                state=state,location_ref=state,legal_actions=[chosen])
            untrained_scoped=b.scoped("aiscend-five-year-blind",state)
            control=b.policy.actions[b.policy.choose(untrained_scoped,explore=True)]
            result={"action":episode["action"],"control_action":control,
                "q_before":q_before,"updates_before":a.policy.steps}
        elif cmd=="feedback":
            xreward=x["reward"]
            result=a.feedback(episode_id=x["episode"],reward=xreward,done=True)
            result["q_after"]=list(a.policy.values(
                a.scoped("aiscend-five-year-blind",x["state"])))
        elif cmd=="checkpoint":
            result=a.checkpoint(baseline=0.,candidate=0.,
                samples=x["samples"],evaluation_sha256=x["digest"])
        elif cmd=="status":
            result={"trained":a.status(),"untrained":b.status()}
        else:
            raise ValueError("Unsupported BEAN worker command")
        sys.stdout.write(json.dumps(result,sort_keys=True)+"\\n")
        sys.stdout.flush()
finally:
    a.close()
    b.close()
"""


class BeanBridge:
    def __init__(self, core_dir, scratch):
        self.identity = core_identity(core_dir)
        env = {
            "PYTHONPATH": str(Path(core_dir).resolve()),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _WORKER,
             str(scratch / "trained-core.sqlite3"),
             str(scratch / "untrained-core.sqlite3")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env)

    def call(self, value):
        self.proc.stdin.write(json.dumps(value, sort_keys=True) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("BEAN bridge ended without a result: "
                               + self.proc.stderr.read()[-1500:])
        return json.loads(line)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc.returncode != 0:
            raise RuntimeError("BEAN worker failed: " + self.proc.stderr.read()[-1000:])
        self.proc.stdout.close()
        self.proc.stderr.close()


def fee_net(buy, sell):
    cost = (FEE_BPS + SPREAD_BPS / 2) / 10000
    return (sell / buy) * (1 - cost) ** 2 - 1


def blind_run(path, core_dir, output):
    """No training/test leakage: 5-year prequential decisions and feedback."""
    from tempfile import TemporaryDirectory
    data, meta = load(path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = (END_EXCLUSIVE - START).days
    dates = [START + timedelta(days=i) for i in range(count)]
    # Initial 84 completed trading days are visible only as market context.
    steps = list(range(LOOKBACK, count - HOLD, STEP))
    equity = {k: {p: 500.0 for p in PRODUCTS} for k in
              ("bean", "untrained", "always_trade", "always_cash")}
    records = []
    digest_tip = "GENESIS"
    checkpoints = []
    with TemporaryDirectory(prefix="aiscend-five-year-blind-") as tmp:
        bridge = BeanBridge(core_dir, Path(tmp))
        try:
            for i in steps:
                decision_day = dates[i]
                entry_day = dates[i+1]
                exit_day = dates[i+HOLD]
                if not decision_day < entry_day < exit_day:
                    raise AssertionError("Noncausal trade window")
                outstanding = []
                # Two separate sleeves, decisions made before ANY price
                # from either symbol's future entry or exit is accessed.
                for p in PRODUCTS:
                    state = signal_state(data, p, decision_day)
                    episode = p + ":" + decision_day.isoformat()
                    decision = bridge.call({
                        "cmd": "choose", "episode": episode, "state": state})
                    row = {
                        "episode": episode, "product": p,
                        "decision_time_utc": decision_day.isoformat() + "T23:59:59Z",
                        "last_feature_date": decision_day.isoformat(),
                        "entry_date": entry_day.isoformat(),
                        "exit_date": exit_day.isoformat(),
                        "state_from_past_only": state,
                        "bean_action_pre_entry": decision["action"],
                        "untrained_action_pre_entry": decision["control_action"],
                        "q_before": decision["q_before"],
                        "updates_before": decision["updates_before"],
                        "prior_sha256": digest_tip,
                    }
                    # The decision is HASH-COMMITTED before future fills
                    # are looked up. This is replay simulation, NOT a
                    # genuine previously timestamped prospective order.
                    row["decision_sha256"] = digest(canon(row))
                    digest_tip = row["decision_sha256"]
                    outstanding.append((p, episode, state, row))

                for p, episode, state, row in outstanding:
                    opening = data[(p, entry_day)]["open"]
                    closing = data[(p, exit_day)]["open"]
                    result = fee_net(opening, closing)
                    for arm, action in (
                        ("bean", row["bean_action_pre_entry"]),
                        ("untrained", row["untrained_action_pre_entry"]),
                        ("always_trade", "trade"),
                        ("always_cash", "cash")):
                        equity[arm][p] *= (1 + result if action == "trade" else 1)
                    reward = 10000.0 * (result if
                        row["bean_action_pre_entry"] == "trade" else 0.0)
                    feedback = bridge.call({
                        "cmd": "feedback", "episode": episode,
                        "state": state, "reward": reward})
                    row.update({
                        "simulated_entry_open": round(opening, 8),
                        "simulated_exit_open": round(closing, 8),
                        "hypothetical_trade_net_return": round(result, 10),
                        "realized_bean_reward": round(reward, 8),
                        "q_after": feedback["q_after"],
                        "updates_after": feedback["q_updates"],
                        "bean_equity_sleeve_usd": round(equity["bean"][p], 8),
                        "untrained_equity_sleeve_usd": round(equity["untrained"][p], 8),
                    })
                    row["outcome_sha256"] = digest(canon(row))
                    digest_tip = row["outcome_sha256"]
                    records.append(row)
                if len(records) - (checkpoints[-1]["samples"] if checkpoints else 0) >= 30:
                    check = bridge.call({
                        "cmd": "checkpoint", "samples": len(records),
                        "digest": digest_tip})
                    checkpoints.append({"samples": len(records),
                        "receipt_sha256": digest_tip,
                        "journal_tip": check["journal_tip"],
                        "promotable": check["promotable"]})
            status = bridge.call({"cmd": "status"})
        finally:
            bridge.close()

    first_entry = dates[steps[0]+1]
    last_exit = dates[steps[-1]+HOLD]
    buy_hold = {}
    for p in PRODUCTS:
        buy_hold[p] = 500.0 * (1 + fee_net(
            data[(p, first_entry)]["open"], data[(p, last_exit)]["open"]))
    totals = {arm: round(sum(sleeves.values()), 4)
              for arm, sleeves in equity.items()}
    totals["buy_hold"] = round(sum(buy_hold.values()), 4)
    summary = {
        "schema": SCHEMA,
        "dataset_sha256": meta["csv_sha256"],
        "dataset_source": meta["source"],
        "from": START.isoformat(),
        "through": (END_EXCLUSIVE - timedelta(days=1)).isoformat(),
        "decision_start": dates[steps[0]].isoformat(),
        "decision_end": dates[steps[-1]].isoformat(),
        "days": count, "model_lookback_days": LOOKBACK,
        "stride_days": STEP, "simulated_holding_days": HOLD-1,
        "fee_bps_each_side": FEE_BPS, "spread_bps": SPREAD_BPS,
        "initial_usd": 1000.0, "equal_initial_sleeves": True,
        "opportunities": len(records), "live_orders": 0,
        "final_equity_usd": totals,
        "net_return_pct": {k: round((v / 1000 - 1) * 100, 4)
                           for k, v in totals.items()},
        "bean_trade_choices": sum(r["bean_action_pre_entry"] == "trade"
                                  for r in records),
        "bean_cash_choices": sum(r["bean_action_pre_entry"] == "cash"
                                 for r in records),
        "bean_core_sha": bridge.identity["sha"],
        "bean_final_q_updates": status["trained"]["q_updates"],
        "checkpoint_count": len(checkpoints),
        "checkpoint_tips": checkpoints,
        "event_tip_sha256": digest_tip,
        "blinding": "external_evaluator_only;BEAN_worker_receives_past_state_then_delayed_reward",
        "sequential_outcomes_not_hidden_from_evaluator": True,
        "parameters_not_tuned_on_holdout": True,
        "fees_modelled_not_venue_fills": True,
        "coinbase_account_verified": False,
        "profitability_independently_proven": False,
        "learned_policy_was_used_in_live_trading": False,
    }
    if len(records) < 300 or summary["bean_final_q_updates"] != len(records):
        raise AssertionError("Five-year real BEAN run incomplete")
    episodes = output.with_name(output.stem + "-episodes.jsonl")
    episodes.write_text("".join(json.dumps(x, sort_keys=True) + "\n"
                                for x in records), encoding="utf-8")
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(json.dumps({"summary": str(output), "episodes": str(episodes),
                      "net_return_pct": summary["net_return_pct"],
                      "opportunities": len(records),
                      "bean_core_sha": summary["bean_core_sha"]}, sort_keys=True))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--core", type=Path)
    parser.add_argument("--output", type=Path, default=Path(
        "artifacts/bean-five-year-blind-summary.json"))
    args = parser.parse_args()
    if args.download == args.run:
        parser.error("Choose exactly one of --download or --run")
    if args.download:
        acquire(args.data)
    else:
        if not args.core:
            parser.error("--run requires a real, separate --core checkout")
        blind_run(args.data, args.core, args.output)


if __name__ == "__main__":
    main()
