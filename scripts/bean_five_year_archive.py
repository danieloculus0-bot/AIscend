"""Durable, verified AIscend BEAN five-year archive.

Pack the full verified public-candle research ledger into a reproducible ZIP,
including the catalog, all per-market research records, history statuses,
configuration and checksums. Restore the ledger for independent tests/recall.
No account secrets or order interfaces are accepted.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from scripts.bean_five_year_ledger import audit_db, canonical
from scripts.bean_five_year_recall import recall_prior
from src.universe_research import _catalog_hash

SCHEMA = "aiscend.bean.five_year.archive.v1"
FILES = ("bean-5y-catalog.json", "bean-5y-universe-summary.json",
         "bean-5y-universe.sqlite3")
ZTIME = (2020, 1, 1, 0, 0, 0)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inspect_inputs(catalog, summary, ledger):
    c = json.loads(Path(catalog).read_text(encoding="utf-8"))
    s = json.loads(Path(summary).read_text(encoding="utf-8"))
    if (c.get("schema") != "aiscend.bean.five_year.catalog.v1"
            or s.get("schema") != "aiscend.bean.five_year.universe.aggregate.v1"
            or c["catalog_hash"] != s["catalog_hash"]
            or c["catalog_hash"] != _catalog_hash(c["eligible_quote_markets"])
            or s["attempted_count"] != c["count"]
            or s.get("promotion_authorized") is not False):
        raise ValueError("ARCHIVE_CATALOG_INCONSISTENT")
    report = audit_db(ledger)
    if report["outcome_count"] != c["count"]:
        raise ValueError("ARCHIVE_INCOMPLETE_LEDGER")
    return c, s, report


def _add(z, name, data):
    info = zipfile.ZipInfo(name, ZTIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 << 16)
    z.writestr(info, data, compresslevel=9)


def create_archive(*, catalog, summary, ledger, output):
    c, s, report = inspect_inputs(catalog, summary, ledger)
    blobs = {
        FILES[0]: Path(catalog).read_bytes(),
        FILES[1]: Path(summary).read_bytes(),
        FILES[2]: Path(ledger).read_bytes(),
    }
    manifest = {"schema": SCHEMA,
                "catalog_hash": c["catalog_hash"],
                "catalog_size": c["count"],
                "experiment_count": len(report["experiments"]),
                "outcome_count": report["outcome_count"],
                "checksums": {k: digest(v) for k, v in sorted(blobs.items())},
                "funded_trade_permission": False,
                "historical_research_only": True}
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A rerun must reproduce exactly the same archive, never modify a prior
    # archive in place with a different data payload.
    with tempfile.TemporaryDirectory() as td:
        candidate = Path(td) / "candidate.zip"
        with zipfile.ZipFile(candidate, "w") as archive:
            for name, blob in sorted(blobs.items()):
                _add(archive, name, blob)
            _add(archive, "manifest.json",
                 (canonical(manifest) + "\n").encode("utf-8"))
        binary = candidate.read_bytes()
    if path.is_file():
        if path.read_bytes() != binary:
            raise FileExistsError("IMMUTABLE_ARCHIVE_CONFLICT")
    else:
        path.write_bytes(binary)
    verify_archive(path)
    return {**manifest, "zip_sha256": digest(binary),
            "archive_file": str(path)}


def verify_archive(path):
    path = Path(path)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if sorted(names) != sorted((*FILES, "manifest.json")):
                raise ValueError("ARCHIVE_UNEXPECTED_FILES")
            meta = json.loads(archive.read("manifest.json"))
            if (meta.get("schema") != SCHEMA
                    or meta.get("historical_research_only") is not True
                    or meta.get("funded_trade_permission") is not False):
                raise ValueError("ARCHIVE_MANIFEST_INVALID")
            for name in FILES:
                raw = archive.read(name)
                if digest(raw) != meta["checksums"].get(name):
                    raise ValueError("ARCHIVE_HASH_MISMATCH")
                (root / name).write_bytes(raw)
        c, s, audit = inspect_inputs(
            root / FILES[0], root / FILES[1], root / FILES[2])
        if (meta["catalog_size"] != c["count"]
                or meta["catalog_hash"] != c["catalog_hash"]
                or meta["outcome_count"] != audit["outcome_count"]
                or meta["experiment_count"] != len(audit["experiments"])):
            raise ValueError("ARCHIVE_REPLAY_MISMATCH")
    return {**meta, "zip_sha256": digest(path.read_bytes()),
            "promotion_authorized": False}


def recall_archive(path, *, product, before_ts):
    # Extract to an ephemeral verified reader; no rewriting the source ZIP.
    verify_archive(path)
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(path) as z:
            db = Path(td) / FILES[2]
            db.write_bytes(z.read(FILES[2]))
        return recall_prior(db, product=product, before_ts=before_ts)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--create", type=Path)
    g.add_argument("--verify", type=Path)
    g.add_argument("--recall", type=Path)
    p.add_argument("--catalog", type=Path)
    p.add_argument("--summary", type=Path)
    p.add_argument("--db", type=Path)
    p.add_argument("--product")
    p.add_argument("--before-ts", type=int)
    args = p.parse_args(argv)
    if args.create:
        if not args.catalog or not args.summary or not args.db:
            p.error("--create needs --catalog, --summary and --db")
        result = create_archive(catalog=args.catalog, summary=args.summary,
                                ledger=args.db, output=args.create)
    elif args.verify:
        result = verify_archive(args.verify)
    else:
        if not args.product or args.before_ts is None:
            p.error("--recall needs --product and --before-ts")
        result = recall_archive(args.recall, product=args.product,
                                before_ts=args.before_ts)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
