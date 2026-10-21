"""Adapter to the real, separately installed BEAN Core, not AIscend's legacy memory class.

BEAN remains advisory: the returned proposal cannot authorize a trade.
Install/checkout BEAN Core separately, then pass its directory explicitly.
"""
from __future__ import annotations
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


def core_identity(core_dir: str | Path) -> dict:
    folder = Path(core_dir).resolve()
    if not (folder / "bean/integration/reasoning_layer.py").is_file():
        raise ValueError("BEAN Core reasoning layer missing")
    result = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True, timeout=10)
    sha = result.stdout.strip()
    if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
        raise ValueError("Invalid BEAN git revision")
    return {"repository": "danieloculus0-bot/BEAN", "sha": sha,
            "source": str(folder), "mode": "advisory_only"}


def observe_with_current_bean(core_dir: str | Path, db_path: str | Path,
                              observation: dict) -> dict:
    """Use BEAN's actual reasoning wrapper in a separate credential-free process.

    Caller supplies a JSON-safe research snapshot; no exchange keys or trade
    executors are passed to the BEAN process. Refuse if Core is unavailable.
    """
    identity = core_identity(core_dir)
    script = r'''
import json, sys
from bean.integration.reasoning_layer import BeanReasoningLayer
payload = json.load(sys.stdin)
with BeanReasoningLayer(payload["db_path"]) as layer:
    response = layer.observe_and_reason(
        "AIscend portfolio allocation review",
        data=payload["observation"], subtype="aiscend_portfolio",
        request_type="analysis", adapter_name="mock")
json.dump({"proposal": response}, sys.stdout, default=str)
'''
    env = {"PYTHONPATH": str(Path(core_dir).resolve()),
           "PATH": os.environ.get("PATH", ""),
           "PYTHONDONTWRITEBYTECODE": "1"}
    payload = {"db_path": str(Path(db_path).resolve()),
               "observation": observation}
    completed = subprocess.run(
        [sys.executable, "-c", script], input=json.dumps(payload),
        text=True, capture_output=True, env=env, timeout=35, check=True)
    result = json.loads(completed.stdout)
    return {"core": identity, "result": result,
            "trade_authorized": False}


def _portfolio_cli(core_dir: str | Path, db_path: str | Path,
                   command: str, *, args=(), payload=None) -> dict:
    """Research-only BEAN CLI; never passes exchange keys or an order executor."""
    core = core_identity(core_dir)
    if not (Path(core_dir) / "bean/optimization/portfolio_cli.py").is_file():
        raise ValueError("BEAN portfolio learning research module missing")
    env = {"PYTHONPATH": str(Path(core_dir).resolve()),
           "PATH": os.environ.get("PATH", ""),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        env["TEMP"] = os.environ.get("TEMP", "")
    proc = subprocess.run(
        [sys.executable, "-m", "bean.optimization.portfolio_cli", command,
         "--db", str(Path(db_path).resolve()), *args],
        input=json.dumps(payload) if payload is not None else None,
        text=True, capture_output=True, check=True, timeout=20,
        cwd=Path(core_dir).resolve(), env=env)
    return {"core": core, "result": json.loads(proc.stdout),
            "trade_authorized": False}


def record_strategy_research_outcome(core_dir: str | Path, db_path: str | Path,
                                     *, strategy: str, event_id: str, state: str,
                                     action: str, reward: float, next_state: str,
                                     evaluation_ref: str, kind: str = "synthetic",
                                     revision: str = "research") -> dict:
    """Only a measured/synthetic *external evaluation*, never a live buy signal.

    Positive and negative rewards update an isolated strategy research policy.
    The application trading/execution layer does not consume this method's result.
    """
    import re
    token = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}\\Z")
    if any(not isinstance(s, str) or not token.fullmatch(s)
           for s in (strategy, event_id, state, action, next_state,
                     evaluation_ref, revision)):
        raise ValueError("invalid experimental learning identity")
    if kind not in ("synthetic", "measured"):
        raise ValueError("unverified results cannot generate learning rewards")
    return _portfolio_cli(core_dir, db_path, "record", payload={
        "app": "AIscend/" + strategy, "event_id": event_id,
        "state": state, "action": action, "reward": reward,
        "next_state": next_state, "done": False,
        "evidence_ref": evaluation_ref, "kind": kind, "revision": revision})


def strategy_research_context(core_dir: str | Path, db_path: str | Path,
                              *, strategy: str, query: str = "",
                              max_bytes: int = 2048) -> dict:
    """Retrieve a small state summary; this NEVER authorizes a trade."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", strategy):
        raise ValueError("invalid strategy")
    return _portfolio_cli(
        core_dir, db_path, "context",
        args=("--app", "AIscend/" + strategy, "--query", query,
              "--max-bytes", str(max_bytes)))
