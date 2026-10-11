"""BEAN Strategy School: bounded mutation lessons scored against historical candles.

No generated code execution and no wallet integration. Students mutate structured
strategy parameters; the historical examiner alone executes a trusted simulator.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from scripts.bean_historical_replay import assess, run

LESSONS = ("momentum", "reversal", "trend", "persistent")


def learn(dataset: dict, rounds: int = 4) -> dict:
    bars = dataset["bars"]
    pairs = tuple(dataset.get("pairs") or bars[0]["prices"])
    cut = int(len(bars) * .6)
    train, holdout = bars[:cut], bars[cut:]
    history = []
    champion = ("cash", 3, 0.0)
    def grade(params):
        result = run(train, *params, pairs=pairs)
        return result["multiple"] - result["max_drawdown"]
    top = grade(champion)
    # Each pass widens the candidate lesson space, but the holdout stays sealed.
    rounds = max(1, min(int(rounds), 32))
    for lesson in range(rounds):
        ideas = [(rule, lookback, threshold)
                 for rule in LESSONS
                 for lookback in (3, 6, 12, 24, 48, 72, 96)
                 for threshold in (0.0, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05)]
        # Distinct examination batches are deterministic and reproducible.
        subset = ideas[lesson::rounds]
        graded = sorted(((grade(p), p) for p in subset), reverse=True)
        best_score, best = graded[0]
        accepted = best_score > top + 0.000001
        if accepted:
            top, champion = best_score, best
        history.append({"lesson": lesson + 1, "candidates": len(subset),
                        "best": list(best), "train_score": round(best_score, 8),
                        "promoted_on_training": accepted})
    final = run(holdout, *champion, pairs=pairs)
    cash = run(holdout, "cash", 3, 0, pairs=pairs)
    btc = run(holdout, "buy_hold", 3, 0, pairs=pairs)
    return {"schema": "bean.strategy.school.v1", "school": "parameter_mutation",
            "model_authored": False, "source": dataset.get("source"),
            "history": history, "candidate_count": sum(x["candidates"] for x in history),
            "champion": list(champion),
            "holdout": final, "cash": cash, "btc": btc,
            "profitable_holdout": final["net_return"] > 0,
            "beats_both_benchmarks": final["final"] > max(cash["final"], btc["final"]),
            "limitation": "Curriculum tests fixed strategy families, not autonomously authored source code."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("artifacts/bean-school.json"))
    args = parser.parse_args()
    result = learn(json.loads(args.dataset.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
