"""Rebuild the attributed, noncommercial Coin Metrics research snapshot.

No Coinbase market data is downloaded. Do not use this CC BY-NC snapshot for
commercial deployment without appropriate data rights. Source CSVs are preserved
semantically, not represented as exchange OHLCV or executable fills.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def fetch(url, timeout=60):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AIscend-Strategy-Research/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def collect(root, cache, cutoff="2026-10-24", years=5, revision=None):
    root, cache = Path(root), Path(cache)
    root.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    until = datetime.fromisoformat(cutoff).replace(tzinfo=timezone.utc)
    since = until - timedelta(days=round(365.25 * years))
    tree_url = "https://api.github.com/repos/coinmetrics/data/git/trees/" + (revision or "master") + "?recursive=1"
    tree = json.loads(fetch(tree_url))
    if tree.get("truncated"):
        raise ValueError("Upstream Git tree is truncated")
    sha = tree["sha"]
    files = {r["path"]: r for r in tree["tree"] if r["path"].startswith("csv/") and r["path"].endswith(".csv")}
    source_url = "https://codeload.github.com/coinmetrics/data/zip/" + sha
    archive_path = cache / (sha + ".zip")
    if not archive_path.exists():
        archive_path.write_bytes(fetch(source_url))
    observed_at = datetime.now(timezone.utc).isoformat()
    archive = zipfile.ZipFile(archive_path)
    prefix = archive.namelist()[0].split("/")[0] + "/"
    target = root / "metric_history"
    target.mkdir(exist_ok=True)
    # Refresh one snapshot without leaving observations from a previous cutoff.
    for old in target.glob("*.jsonl.gz"):
        old.unlink()
    manifest = {"schema": "aiscend.history_manifest.v1", "provider": "Coin Metrics",
                "repository": "https://github.com/coinmetrics/data", "source_git_tree": sha,
                "source_archive_url": source_url, "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                "license": "CC-BY-NC-4.0", "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
                "use_scope": "noncommercial_research; commercial rights not established",
                "observed_at": observed_at, "requested_start": since.isoformat(), "requested_end_exclusive": until.isoformat(),
                "universe": "ALL upstream CSV assets, including inactive and non-crypto assets when present; execution eligibility unverified",
                "point_in_time": False, "revision_warning": "Backfilled current-vintage metrics. No historical first-seen or entity-label vintages. Do not treat this as a point-in-time on-chain/news dataset.",
                "not_ohlcv": True, "assets": [], "total_rows": 0}
    for n, path in enumerate(sorted(files), 1):
        source = archive.read(prefix + path)
        asset = Path(path).stem
        rows = []
        names = set()
        for raw in csv.DictReader(io.StringIO(source.decode("utf-8-sig"))):
            date = raw.pop("time", raw.pop("date", ""))
            try:
                ts = datetime.fromisoformat(date.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if not since <= ts < until:
                continue
            values = {k: v for k, v in raw.items() if v not in ("", None)}
            if not values:
                continue
            names.update(values)
            rows.append({"asset": asset, "time": ts.isoformat(), "values": values})
        entry = {"asset": asset, "source_path": path, "source_blob": files[path]["sha"],
                 "source_csv_sha256": hashlib.sha256(source).hexdigest(), "rows": len(rows),
                 "metrics": sorted(names), "has_price_usd": bool({"PriceUSD", "ReferenceRateUSD"} & names),
                 "price_metrics": sorted({"PriceUSD", "ReferenceRateUSD"} & names),
                 "status": "COLLECTED" if rows else "NO_ROWS_IN_WINDOW"}
        if rows:
            rows.sort(key=lambda r: r["time"])
            if len({r["time"] for r in rows}) != len(rows):
                raise ValueError("Duplicate source dates: " + asset)
            dest = target / (asset + ".jsonl.gz")
            content = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows).encode()
            dest.write_bytes(gzip.compress(content, mtime=0))
            entry.update({"path": str(dest.relative_to(root)), "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                          "first_time": rows[0]["time"], "last_time": rows[-1]["time"]})
            manifest["total_rows"] += len(rows)
        manifest["assets"].append(entry)
        if n % 100 == 0:
            print(json.dumps({"processed_assets": n, "total_assets": len(files), "rows": manifest["total_rows"]}), flush=True)
    manifest["asset_files_found"] = len(files)
    manifest["assets_with_rows"] = sum(a["rows"] > 0 for a in manifest["assets"])
    manifest["assets_with_price"] = sum(a["has_price_usd"] for a in manifest["assets"])
    (root / "history_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    license_bytes = archive.read(prefix + "LICENSE")
    (root / "COIN_METRICS_DATA_LICENSE.txt").write_bytes(license_bytes)
    print(json.dumps({k: manifest[k] for k in ("asset_files_found", "assets_with_rows", "assets_with_price", "total_rows", "source_git_tree")}), flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--cache", type=Path, default=Path("artifacts/strategy-history-download"))
    parser.add_argument("--cutoff", default="2026-10-24")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--revision")
    args = parser.parse_args()
    collect(args.root, args.cache, args.cutoff, args.years, args.revision)
