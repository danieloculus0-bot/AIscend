"""Run a read-only AIscend portfolio snapshot through actual BEAN Core.

Usage:
 python -m scripts.bean_portfolio_review --core ../BEAN --snapshot snapshot.json
No wallet connection, exchange execution, or credential retrieval is performed.
"""
import argparse
import json
from pathlib import Path
from src.bean_core_bridge import observe_with_current_bean


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=Path("bean-aiscend-review.db"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/bean-core-review.json"))
    args = parser.parse_args()
    raw = json.loads(args.snapshot.read_text(encoding="utf-8"))
    # Deliberate allowlist: no accidental exchange credentials to BEAN subprocess.
    observation = {
        "positions": raw.get("positions", {}),
        "rotation_reviews": raw.get("rotation_reviews", []),
        "net_liquidation": raw.get("net_liquidation"),
        "market_radar": raw.get("market_radar", {}),
    }
    report = observe_with_current_bean(args.core, args.db, observation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, default=str, indent=2) + "\n")
    print("BEAN revision:", report["core"]["sha"])
    print("Read-only proposal saved:", args.output)


if __name__ == "__main__":
    main()
