"""Source-linked strategy research and compressed historical observations.

This module does not import execution rails or authenticate to any exchange.
An executable snapshot rule is not a simulator, trained model or trade approval.
"""
from __future__ import annotations
import ast
import gzip
import hashlib
import io
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "aiscend.strategy_atlas.v1"
ROOT_CAUSES = {"UN":"unknown or inconclusive", "LC":"documented costs exceed gross edge",
               "OV":"documented selection/overfitting failure", "RG":"documented regime mismatch",
               "TS":"documented timing leakage", "LP":"documented liquidity constraint",
               "CF":"documented capital/constraint failure", "MI":"missing required data",
               "OK":"positive test outcome, not proof of future profit"}

DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY,title TEXT NOT NULL,url TEXT NOT NULL UNIQUE,type TEXT NOT NULL,result TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE strategies(id TEXT PRIMARY KEY,name TEXT NOT NULL,family TEXT NOT NULL,implementation_status TEXT NOT NULL,evidence_status TEXT NOT NULL,spec_json TEXT NOT NULL);
CREATE INDEX strategy_family ON strategies(family);
CREATE TABLE strategy_sources(strategy_id TEXT REFERENCES strategies(id),source_id TEXT REFERENCES sources(id),PRIMARY KEY(strategy_id,source_id));
CREATE TABLE data_sources(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,rights TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE history_chunks(asset TEXT PRIMARY KEY,first_time TEXT,last_time TEXT,rows INTEGER NOT NULL CHECK(rows>=0),price_metrics_json TEXT NOT NULL,metrics_json TEXT NOT NULL,sha256 TEXT NOT NULL,payload_gzip BLOB NOT NULL);
CREATE TABLE documents(id TEXT PRIMARY KEY,canonical_url TEXT NOT NULL,published_at REAL,first_seen_at REAL NOT NULL,available_at REAL NOT NULL,cluster_id TEXT NOT NULL,source_group TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE INDEX document_time ON documents(available_at);
CREATE TABLE experiment_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,previous_hash TEXT NOT NULL,entry_hash TEXT NOT NULL UNIQUE,record_json TEXT NOT NULL);
CREATE TRIGGER no_experiment_update BEFORE UPDATE ON experiment_events BEGIN SELECT RAISE(ABORT,'append-only experiment evidence'); END;
CREATE TRIGGER no_experiment_delete BEFORE DELETE ON experiment_events BEGIN SELECT RAISE(ABORT,'append-only experiment evidence'); END;
"""

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def utc_seconds(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if not math.isfinite(value):raise ValueError("Nonfinite timestamp")
        return float(value)
    result=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp requires explicit timezone")
    return result.timestamp()

def iso_time(value):
    return datetime.fromtimestamp(utc_seconds(value),timezone.utc).isoformat()

def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]

def build_database(root,output):
    """Build atomically from authored specs and checksum-verified history chunks."""
    root,output=Path(root),Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_name(output.name+".building")
    if temp.exists():temp.unlink()
    con=sqlite3.connect(temp)
    try:
        con.executescript(DDL)
        con.execute("INSERT INTO metadata VALUES (?,?)",("schema",canonical(SCHEMA)))
        for name in ("sources","strategies","datasets"):
            for row in read_jsonl(root/(name+".jsonl")):
                if name=="sources":
                    con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?)",(row["id"],row["title"],row["url"],row["type"],row["reported_result"],canonical(row)))
                elif name=="strategies":
                    validate_recipe(row)
                    con.execute("INSERT INTO strategies VALUES (?,?,?,?,?,?)",(row["id"],row["name"],row["family"],row["implementation_status"],row["evidence_status"],canonical(row)))
                    con.executemany("INSERT INTO strategy_sources VALUES (?,?)",[(row["id"],s) for s in row["source_ids"]])
                else:
                    con.execute("INSERT INTO data_sources VALUES (?,?,?,?,?)",(row["id"],row["kind"],row["status"],row["rights"],canonical(row)))
        manifest_path=root/"history_manifest.json"
        if manifest_path.exists():
            manifest=json.loads(manifest_path.read_text())
            for entry in manifest["assets"]:
                if not entry.get("path"):continue
                history_path=(root/entry["path"]).resolve()
                if not history_path.is_relative_to(root.resolve()):raise ValueError("History path escapes dataset root")
                content=history_path.read_bytes()
                if hashlib.sha256(content).hexdigest()!=entry["sha256"]:raise ValueError("History checksum changed: "+entry["asset"])
                # Validate recorded count and content structure before publishing a DB.
                count=0
                with gzip.open(io.BytesIO(content),"rt",encoding="utf-8") as stream:
                    previous=None
                    for line in stream:
                        row=json.loads(line)
                        ts=utc_seconds(row["time"])
                        if row["asset"]!=entry["asset"] or (previous is not None and ts<=previous):raise ValueError("Invalid history ordering/asset")
                        if not isinstance(row["values"],dict):raise ValueError("Invalid metric payload")
                        previous=ts;count+=1
                if count!=entry["rows"]:raise ValueError("History row count changed")
                price_metrics=sorted(set(entry["metrics"]) & {"PriceUSD","ReferenceRateUSD"})
                con.execute("INSERT INTO history_chunks VALUES (?,?,?,?,?,?,?,?)",(entry["asset"],entry["first_time"],entry["last_time"],count,canonical(price_metrics),canonical(entry["metrics"]),entry["sha256"],content))
            con.execute("INSERT INTO metadata VALUES (?,?)",("history_manifest",canonical(manifest)))
        sentiment=root/"fear_greed_history.json"
        if sentiment.exists():con.execute("INSERT INTO metadata VALUES (?,?)",("fear_greed_history",canonical(json.loads(sentiment.read_text()))))
        audit=root/"collection_audit.json"
        if audit.exists():con.execute("INSERT INTO metadata VALUES (?,?)",("collection_audit",canonical(json.loads(audit.read_text()))))
        if list(con.execute("PRAGMA foreign_key_check")):raise ValueError("Invalid source relation")
        con.commit()
        if con.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Database integrity failure")
    except BaseException:
        con.close()
        temp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(temp,output)
    return output

def compress_database(database,destination):
    data=Path(database).read_bytes()
    Path(destination).write_bytes(gzip.compress(data,compresslevel=9,mtime=0))
    return {"path":str(destination),"sha256":hashlib.sha256(Path(destination).read_bytes()).hexdigest(),"uncompressed_sha256":hashlib.sha256(data).hexdigest(),"uncompressed_bytes":len(data),"compressed_bytes":Path(destination).stat().st_size}

class Atlas:
    """Read-only source of records; compressed snapshots open into a temporary file."""
    def __init__(self,path):
        self.path=Path(path)
        self._temporary=None
        target=self.path.resolve()
        if self.path.suffix==".gz":
            self._temporary=tempfile.TemporaryDirectory(prefix="aiscend-research-")
            target=Path(self._temporary.name)/"atlas.sqlite3"
            with gzip.open(self.path,"rb") as source,target.open("wb") as output:
                import shutil
                shutil.copyfileobj(source,output)
        self.con=sqlite3.connect(target.as_uri()+"?mode=ro",uri=True)
        self.con.row_factory=sqlite3.Row
        row=self.con.execute("SELECT value_json FROM metadata WHERE key='schema'").fetchone()
        if not row or json.loads(row[0])!=SCHEMA:raise ValueError("Unknown atlas schema")
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):
        self.con.close()
        if self._temporary:self._temporary.cleanup()
    def stats(self):
        counts={table:self.con.execute("SELECT COUNT(*) FROM "+table).fetchone()[0] for table in ("sources","strategies","data_sources","history_chunks","documents","experiment_events")}
        counts["historical_asset_days"]=self.con.execute("SELECT COALESCE(SUM(rows),0) FROM history_chunks").fetchone()[0]
        counts["assets_with_usd_prices"]=self.con.execute("SELECT COUNT(*) FROM history_chunks WHERE price_metrics_json != '[]'").fetchone()[0]
        counts["families"]={r[0]:r[1] for r in self.con.execute("SELECT family,COUNT(*) FROM strategies GROUP BY family")}
        return counts
    def query(self,*,family=None,text=None):
        clause=[];params=[]
        if family:clause.append("family=?");params.append(family)
        if text:clause.append("(name LIKE ? OR id LIKE ? OR spec_json LIKE ?)");params.extend(["%"+text+"%"]*3)
        sql="SELECT spec_json FROM strategies"+(" WHERE "+" AND ".join(clause) if clause else "")+" ORDER BY id"
        return [json.loads(r[0]) for r in self.con.execute(sql,params)]
    def recipe(self,strategy_id):
        row=self.con.execute("SELECT spec_json FROM strategies WHERE id=?",(strategy_id,)).fetchone()
        if not row:raise KeyError(strategy_id)
        return json.loads(row[0])
    def history(self,asset,*,metric=None,start=None,end=None):
        row=self.con.execute("SELECT payload_gzip,sha256 FROM history_chunks WHERE asset=?",(asset,)).fetchone()
        if not row:raise KeyError(asset)
        data=row[0]
        if hashlib.sha256(data).hexdigest()!=row[1]:raise ValueError("Historical chunk checksum mismatch")
        start_ts=utc_seconds(start) if start is not None else -math.inf
        end_ts=utc_seconds(end) if end is not None else math.inf
        with gzip.open(io.BytesIO(data),"rt",encoding="utf-8") as stream:
            for line in stream:
                item=json.loads(line);ts=utc_seconds(item["time"])
                if not start_ts<=ts<end_ts:continue
                if metric is not None:
                    if metric not in item["values"]:continue
                    yield {"asset":asset,"time":item["time"],"metric":metric,"value":item["values"][metric],"point_in_time":False,"source":"Coin Metrics, CC-BY-NC-4.0"}
                else:yield item
    def metadata(self,key):
        row=self.con.execute("SELECT value_json FROM metadata WHERE key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else None

class MissingFeature(ValueError):pass

FUNCTIONS={"abs":abs,"min":min,"max":max,"log":math.log,"sqrt":math.sqrt,
           "sign":lambda x:1 if x>0 else (-1 if x<0 else 0)}

def expression_names(expression):
    tree=ast.parse(expression,mode="eval")
    allowed=(ast.Expression,ast.BoolOp,ast.And,ast.Or,ast.UnaryOp,ast.Not,ast.USub,ast.UAdd,
             ast.BinOp,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow,ast.Compare,
             ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.Eq,ast.NotEq,ast.Name,ast.Load,ast.Constant,ast.Call)
    for node in ast.walk(tree):
        if not isinstance(node,allowed):raise ValueError("Unsupported rule syntax: "+type(node).__name__)
        if isinstance(node,ast.Call) and (not isinstance(node.func,ast.Name) or node.func.id not in FUNCTIONS or node.keywords):raise ValueError("Unsupported rule function")
        if isinstance(node,ast.Constant) and (isinstance(node.value,bool) or not isinstance(node.value,(int,float))):raise ValueError("Only numeric literals allowed")
        if isinstance(node,ast.Constant) and (not math.isfinite(node.value) or abs(node.value)>1e12):raise ValueError("Invalid numeric literal")
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Pow):
            if not isinstance(node.right,ast.Constant) or abs(node.right.value)>8:raise ValueError("Exponent outside research rule limits")
    return {n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}-set(FUNCTIONS)

def evaluate_expression(expression,features):
    names=expression_names(expression)
    for name in names:
        if name not in features or features[name] is None:raise MissingFeature(name)
        if isinstance(features[name],bool) or not isinstance(features[name],(int,float)) or not math.isfinite(features[name]):raise MissingFeature(name+" must be finite numeric input")
    tree=ast.parse(expression,mode="eval")
    def visit(n):
        if isinstance(n,ast.Expression):return visit(n.body)
        if isinstance(n,ast.Constant):return n.value
        if isinstance(n,ast.Name):return features[n.id]
        if isinstance(n,ast.Call):return FUNCTIONS[n.func.id](*[visit(x) for x in n.args])
        if isinstance(n,ast.UnaryOp):
            x=visit(n.operand)
            if isinstance(n.op,ast.Not):return not x
            return -x if isinstance(n.op,ast.USub) else x
        if isinstance(n,ast.BoolOp):
            return all(visit(x) for x in n.values) if isinstance(n.op,ast.And) else any(visit(x) for x in n.values)
        if isinstance(n,ast.BinOp):
            x,y=visit(n.left),visit(n.right)
            op=n.op
            if isinstance(op,ast.Add):return x+y
            if isinstance(op,ast.Sub):return x-y
            if isinstance(op,ast.Mult):return x*y
            if isinstance(op,ast.Div):return x/y
            if isinstance(op,ast.Mod):return x%y
            if isinstance(op,ast.Pow):return x**y
        if isinstance(n,ast.Compare):
            left=visit(n.left)
            for op,r in zip(n.ops,n.comparators):
                right=visit(r)
                if isinstance(op,ast.Lt):ok=left<right
                elif isinstance(op,ast.LtE):ok=left<=right
                elif isinstance(op,ast.Gt):ok=left>right
                elif isinstance(op,ast.GtE):ok=left>=right
                elif isinstance(op,ast.Eq):ok=left==right
                else:ok=left!=right
                if not ok:return False
                left=right
            return True
        raise ValueError("Invalid expression")
    value=visit(tree)
    if isinstance(value,(float,int)) and not math.isfinite(value):raise ValueError("Nonfinite expression result")
    return value

def validate_recipe(recipe):
    if not recipe.get("algorithm_steps") or not recipe.get("source_ids"):raise ValueError("Unspecified strategy")
    if recipe["implementation_status"]=="executable_snapshot_rule":
        defined=set(recipe["snapshot_features"])
        for key in ("entry","exit","side_expression"):
            if expression_names(recipe[key])-defined:raise ValueError("Undeclared feature: "+recipe["id"])

def evaluate_recipe(recipe,features,*,decision_at,feature_available_at,position=0):
    """Return a research target direction; missing inputs never become neutral votes."""
    validate_recipe(recipe)
    if recipe["implementation_status"]!="executable_snapshot_rule":
        return {"strategy_id":recipe["id"],"status":"SPECIALIZED_ENGINE_REQUIRED","algorithm_steps":recipe["algorithm_steps"],"trade_authorized":False}
    if position not in (-1,0,1):raise ValueError("position must be -1, 0 or 1")
    asof=utc_seconds(decision_at)
    required=set().union(*(expression_names(recipe[k]) for k in ("entry","exit","side_expression")))
    for name in required:
        if name not in feature_available_at:raise MissingFeature(name+" availability time missing")
        if utc_seconds(feature_available_at[name])>asof:raise ValueError("Feature from future: "+name)
    # Validate ALL required data even if a boolean short-circuit could hide it.
    for name in required:
        evaluate_expression(name,features)
    leave=bool(evaluate_expression(recipe["exit"],features))
    enter=bool(evaluate_expression(recipe["entry"],features))
    side=evaluate_expression(recipe["side_expression"],features)
    if side not in (-1,0,1):raise ValueError("Invalid target side")
    target=0 if position and leave else (position or (int(side) if enter else 0))
    return {"strategy_id":recipe["id"],"status":"RESEARCH_RULE_EVALUATED","target_direction":target,
            "entry_condition":enter,"exit_condition":leave,"decision_at":iso_time(asof),
            "source_ids":recipe["source_ids"],"recipe_sha256":digest(recipe),
            "input_sha256":digest({k:features[k] for k in sorted(required)}),"trade_authorized":False,
            "scope":"Signal-direction evaluation only; sizing, model fitting, fills, short availability and profitability are not evaluated."}

def append_experiment(database,record):
    """Preserve complete results, including failures, in a verified hash chain."""
    required={"experiment_id","strategy_id","dataset_sha256","config","split","started_at","finished_at","observed_at","result","metrics","reason_code","evidence_ids"}
    if not required<=record.keys():raise ValueError("Incomplete experiment record: "+str(sorted(required-record.keys())))
    if record["reason_code"] not in ROOT_CAUSES:raise ValueError("Unknown reason code")
    if not utc_seconds(record["started_at"])<=utc_seconds(record["finished_at"])<=utc_seconds(record["observed_at"]):raise ValueError("Invalid experiment chronology")
    if record["result"] not in ("POSITIVE","NEGATIVE","INCONCLUSIVE","DATA_UNAVAILABLE","ERROR"):raise ValueError("Unknown result")
    raw=canonical(record)
    with sqlite3.connect(database) as con:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM strategies WHERE id=?",(record["strategy_id"],)).fetchone():raise ValueError("Unregistered strategy")
        previous=con.execute("SELECT entry_hash FROM experiment_events ORDER BY seq DESC LIMIT 1").fetchone()
        previous=previous[0] if previous else "0"*64
        current=hashlib.sha256((previous+"\n"+raw).encode()).hexdigest()
        con.execute("INSERT INTO experiment_events(previous_hash,entry_hash,record_json) VALUES (?,?,?)",(previous,current,raw))
    return current

def recall_experiments(database,*,as_of,strategy_id=None):
    asof=utc_seconds(as_of);previous="0"*64;out=[]
    with sqlite3.connect(database) as con:
        for row in con.execute("SELECT previous_hash,entry_hash,record_json FROM experiment_events ORDER BY seq"):
            if row[0]!=previous or hashlib.sha256((previous+"\n"+row[2]).encode()).hexdigest()!=row[1]:raise ValueError("Experiment ledger tampered")
            previous=row[1];r=json.loads(row[2])
            if utc_seconds(r["observed_at"])<=asof and utc_seconds(r["finished_at"])<=asof and (strategy_id is None or r["strategy_id"]==strategy_id):out.append(r)
    return out
