"""One-shot, immutable public Coinbase BEAN paper observer with durable evidence.

Called by the main-branch GitHub Actions schedule. No keys, account API,
live orders, strategy promotion, or retuning. The record branch retains the
SAME SQLite database across workflow runs; a fresh CI artifact alone is not
durable evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.bean_sixhour_forward import (
    inspect, verify_database, backup_database, digest,
)

SOURCE_PATHS = (
    "src/bean_sixhour_forward.py",
    "src/bean_profit_lab.py",
    "src/bean_six_hour_universe.py",
    "src/bean_strategy_lab.py",
    "src/core.py",
)


def source_fingerprint(repo_root, files=SOURCE_PATHS):
    """Bind a prospective ledger to the exact decision engine source bytes."""
    repo_root = Path(repo_root)
    h = hashlib.sha256()
    for relative in files:
        raw = (repo_root / relative).read_bytes()
        h.update(relative.encode("utf-8") + b"\0")
        h.update(len(raw).to_bytes(8, "big"))
        h.update(raw)
    return h.hexdigest()


def pin_source(state_dir, source_root, files=SOURCE_PATHS):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    pin = state_dir / "engine.sha256"
    database = state_dir / "prospective.sqlite3"
    calculated = source_fingerprint(source_root, files=files)
    if pin.exists():
        if pin.read_text(encoding="ascii").strip() != calculated:
            raise ValueError("Prospective paper engine changed; review before continuing frozen ledger")
        if not database.is_file():
            raise ValueError("Pinned prospective SQLite database missing; refusing silent ledger reset")
    else:
        if database.exists():
            raise ValueError("Existing prospective SQLite is missing its engine pin")
        pin.write_text(calculated + "\n", encoding="ascii")
    return calculated


def collect(state_root, manifest_path, source_root, run_key, backup_to):
    """One observation only. Verify the same SQLite before writing any report."""
    state_root = Path(state_root)
    evidence = state_root / "evidence"
    reports = state_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    if not run_key or not all(c.isalnum() or c in "-_" for c in run_key):
        raise ValueError("Invalid immutable workflow run identity")
    report_path = reports / (run_key + ".json")
    if report_path.exists():
        raise FileExistsError("Cannot overwrite an earlier paper observation")

    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("promotion_authorized") is not False:
        raise ValueError("Unexpected promotion permission in paper manifest")
    expected_markets = {c["product"] for c in manifest["candidates"]}
    if len(expected_markets) != len(manifest["candidates"]):
        raise ValueError("Duplicate paper candidate identity")

    engine_sha = pin_source(evidence, source_root)
    database = evidence / "prospective.sqlite3"
    latest_path = evidence / "latest.json"
    # A valid older SQLite backup still has a valid internal hash chain.
    # Anchor it to the last independently committed report BEFORE appending.
    if database.exists():
        if not latest_path.is_file():
            raise ValueError("Existing prospective SQLite has no retained latest report")
        previous = json.loads(latest_path.read_text(encoding="utf-8"))
        verified_previous = verify_database(manifest_path, database)
        if (verified_previous["status"] != "LEDGER_VERIFIED"
                or verified_previous["ledger_tip"] != previous.get("ledger_tip")
                or verified_previous["events"] != previous.get("event_count")
                or verified_previous["manifest_sha"] != previous.get("manifest_sha")
                or previous.get("source_sha256") != engine_sha):
            raise ValueError("Prospective SQLite continuity mismatch with retained report")
    elif latest_path.exists():
        raise ValueError("Retained prospective report has no SQLite database")
    observed = inspect(manifest_path, database)
    if (observed.get("status") != "OBSERVED" or
            observed.get("promotion_authorized") is not False or
            set(observed.get("markets", {})) != expected_markets):
        raise ValueError("Incomplete or unauthorized paper observation")

    verified = verify_database(manifest_path, database)
    if (verified["status"] != "LEDGER_VERIFIED" or
            verified["ledger_tip"] != observed["ledger_tip"] or
            verified["manifest_sha"] != digest(manifest)):
        raise ValueError("Prospective observation and SQLite disagree")

    backup = backup_database(manifest_path, database, backup_to)
    if backup["ledger_tip"] != verified["ledger_tip"]:
        raise ValueError("Verified paper backup does not match original")

    summary = {
        "schema": "aiscend.bean.hosted.public.paper.v1",
        "run_key": run_key,
        "as_of_unix": observed["as_of_unix"],
        "source_sha256": engine_sha,
        "manifest_sha": verified["manifest_sha"],
        "ledger_tip": verified["ledger_tip"],
        "event_count": verified["events"],
        "sqlite_backup_sha256": backup["sha256"],
        "markets": observed["markets"],
        "promotion_authorized": False,
        "funded_orders": False,
        "limitations": "Public completed candles and hypothetical next-open orders; no actual Coinbase fills or forward profit claim.",
    }
    report_path.write_text(json.dumps(summary, sort_keys=True, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")
    (evidence / "latest.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "status": "HOSTED_PAPER_RETAINED",
        "run_key": run_key,
        "ledger_tip": verified["ledger_tip"],
        "event_count": verified["events"],
        "market_statuses": {k: v["status"] for k, v in observed["markets"].items()},
        "promotion_authorized": False,
    }, sort_keys=True))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=Path(
        "research/bean_frozen_prospective_20261023_fee90.json"))
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--backup-to", type=Path, required=True)
    opts = parser.parse_args(argv)
    collect(opts.state_root, opts.manifest, opts.source_root,
            opts.run_key, opts.backup_to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
