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
