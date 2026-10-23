"""Versioned data-subset trees for BEAN's compact, typed-pair memory.

Stored codes remain compact (1-8 typed pairs); the || notation
is a DISPLAY marker, not persisted bytes. Any node may own multiple named
child reference sets. Each node has immutable meaning, stage, timestamp, and
evidence pointer. Previous information cannot be silently reinterpreted.

This SQLite ledger complements, never replaces, source trade decisions.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .bean_pair_index import parse

SCHEMA = "aiscend.bean.memory-subsets.v1"
STAGES = {"signal", "decision", "outcome", "root_cause", "definition"}
_SET = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,47}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def hash_record(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_code(code):
    """Reject mixed-class pairs, zero-width delimiters, wildcard definitions."""
    if not isinstance(code, str) or len(code) not in range(2, 17, 2):
        raise ValueError("Data subset index needs 1-8 complete pairs")
    parse(code, pattern=True)
    if " " in code:
        raise ValueError("Spaces are wildcard QUERY characters, not saved codes")
    return code


def utc_time(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Timestamp must be explicit UTC ending Z")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError("Timestamp must be UTC")
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


class SubsetLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS ref_sets(
          set_id TEXT PRIMARY KEY, description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nodes(
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          node_id TEXT NOT NULL UNIQUE,
          parent_id TEXT,
          set_id TEXT NOT NULL,
          code TEXT NOT NULL,
          meaning TEXT NOT NULL,
          stage TEXT NOT NULL,
          available_at TEXT NOT NULL,
          evidence_sha256 TEXT NOT NULL,
          source_episode TEXT NOT NULL,
          prior_sha256 TEXT NOT NULL,
          receipt_sha256 TEXT NOT NULL UNIQUE,
          FOREIGN KEY(parent_id) REFERENCES nodes(node_id),
          FOREIGN KEY(set_id) REFERENCES ref_sets(set_id),
          UNIQUE(parent_id,set_id,code,source_episode)
        );
        CREATE TABLE IF NOT EXISTS character_types(
          scope TEXT NOT NULL,
          set_id TEXT NOT NULL,
          slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 8),
          type_character TEXT NOT NULL,
          meaning TEXT NOT NULL,
          PRIMARY KEY(scope,slot,type_character),
          FOREIGN KEY(set_id) REFERENCES ref_sets(set_id)
        );
        CREATE TABLE IF NOT EXISTS character_subsets(
          scope TEXT NOT NULL,
          slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 8),
          type_character TEXT NOT NULL,
          subset_character TEXT NOT NULL,
          meaning TEXT NOT NULL,
          PRIMARY KEY(scope,slot,type_character,subset_character),
          FOREIGN KEY(scope,slot,type_character)
            REFERENCES character_types(scope,slot,type_character)
        );
        CREATE TRIGGER IF NOT EXISTS freeze_character_types_update
          BEFORE UPDATE ON character_types BEGIN SELECT RAISE(ABORT,'immutable data type'); END;
        CREATE TRIGGER IF NOT EXISTS freeze_character_types_delete
          BEFORE DELETE ON character_types BEGIN SELECT RAISE(ABORT,'immutable data type'); END;
        CREATE TRIGGER IF NOT EXISTS freeze_character_subsets_update
          BEFORE UPDATE ON character_subsets BEGIN SELECT RAISE(ABORT,'immutable subset'); END;
        CREATE TRIGGER IF NOT EXISTS freeze_character_subsets_delete
          BEFORE DELETE ON character_subsets BEGIN SELECT RAISE(ABORT,'immutable subset'); END;
        CREATE INDEX IF NOT EXISTS nodes_children
          ON nodes(parent_id,set_id,available_at);
        CREATE INDEX IF NOT EXISTS nodes_source
          ON nodes(source_episode,available_at);
        CREATE TRIGGER IF NOT EXISTS freeze_nodes_update
          BEFORE UPDATE ON nodes BEGIN
          SELECT RAISE(ABORT,'immutable subset node'); END;
        CREATE TRIGGER IF NOT EXISTS freeze_nodes_delete
          BEFORE DELETE ON nodes BEGIN
          SELECT RAISE(ABORT,'immutable subset node'); END;
        CREATE TRIGGER IF NOT EXISTS freeze_sets_update
          BEFORE UPDATE ON ref_sets BEGIN
          SELECT RAISE(ABORT,'immutable reference set'); END;
        CREATE TRIGGER IF NOT EXISTS freeze_sets_delete
          BEFORE DELETE ON ref_sets BEGIN
          SELECT RAISE(ABORT,'immutable reference set'); END;
        """)
        self.db.commit()

    def define_set(self, set_id, description):
        if not isinstance(set_id, str) or not _SET.fullmatch(set_id):
            raise ValueError("Invalid reference-set identifier")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Reference set needs a description")
        row = self.db.execute(
            "SELECT description FROM ref_sets WHERE set_id=?",
            (set_id,)).fetchone()
        if row is not None:
            if row["description"] != description:
                raise ValueError("Existing reference table cannot be redefined")
            return set_id
        with self.db:
            self.db.execute("INSERT INTO ref_sets VALUES(?,?)",
                            (set_id, description))
        return set_id

    def add(self, *, set_id, code, meaning, stage, available_at,
            evidence_sha256, source_episode, parent_id=None):
        validate_code(code)
        at = utc_time(available_at)
        if (stage not in STAGES or not isinstance(meaning, str)
                or not meaning.strip() or len(meaning) > 500
                or not isinstance(source_episode, str) or not source_episode.strip()
                or not isinstance(evidence_sha256, str)
                or not _SHA.fullmatch(evidence_sha256)):
            raise ValueError("Invalid evidence, definition or inspection stage")
        if not self.db.execute(
                "SELECT 1 FROM ref_sets WHERE set_id=?", (set_id,)).fetchone():
            raise ValueError("Unknown reference set")
        if parent_id is not None:
            parent = self.db.execute(
                "SELECT available_at FROM nodes WHERE node_id=?",
                (parent_id,)).fetchone()
            if parent is None:
                raise ValueError("Missing parent subset")
            if at < parent["available_at"]:
                raise ValueError("Child cannot predate parent")
        payload = {
            "set_id":set_id,"code":code,"meaning":meaning,"stage":stage,
            "available_at":at,"evidence_sha256":evidence_sha256,
            "source_episode":source_episode,"parent_id":parent_id,
        }
        node_id=hash_record(payload)
        existing=self.db.execute(
            "SELECT node_id FROM nodes WHERE parent_id IS ? AND set_id=?"
            " AND code=? AND source_episode=?",
            (parent_id,set_id,code,source_episode)).fetchone()
        if existing:
            if existing["node_id"] != node_id:
                raise ValueError("Existing subset definition cannot be changed")
            return node_id
        last=self.db.execute(
            "SELECT receipt_sha256 FROM nodes ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prior=last[0] if last else "GENESIS"
        receipt=hash_record({"prior_sha256":prior,"payload":payload})
        with self.db:
            self.db.execute("""
              INSERT INTO nodes
              (node_id,parent_id,set_id,code,meaning,stage,available_at,
               evidence_sha256,source_episode,prior_sha256,receipt_sha256)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (node_id,parent_id,set_id,code,meaning,stage,at,
               evidence_sha256,source_episode,prior,receipt))
        return node_id

    def children(self, parent_id, *, as_of, set_id=None):
        as_of=utc_time(as_of)
        sql=("SELECT node_id,set_id,code,meaning,stage,available_at,"
             "evidence_sha256 FROM nodes WHERE parent_id IS ?"
             " AND available_at <= ?")
        args=[parent_id,as_of]
        if set_id is not None:
            sql+=" AND set_id=?"
            args.append(set_id)
        sql+=" ORDER BY set_id,code,node_id"
        return [dict(r) for r in self.db.execute(sql,args)]

    def verify(self):
        records=self.db.execute("SELECT * FROM nodes ORDER BY seq").fetchall()
        tip="GENESIS"
        for seq,row in enumerate(records,1):
            payload={key:row[key] for key in (
                "set_id","code","meaning","stage","available_at",
                "evidence_sha256","source_episode","parent_id")}
            if (row["seq"] != seq or row["node_id"] != hash_record(payload)
                    or row["prior_sha256"] != tip
                    or row["receipt_sha256"] != hash_record({
                        "prior_sha256":tip,"payload":payload})):
                raise ValueError("Subset ledger SHA chain failed")
            tip=row["receipt_sha256"]
        return {"schema":SCHEMA,"nodes":len(records),
                "receipt_tip_sha256":tip}

    def close(self):
        self.db.close()
