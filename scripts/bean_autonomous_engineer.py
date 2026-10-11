"""BEAN local autonomous AIscend engineer. No human-per-iteration approval.

Run with an installed Ollama coding model on the local Windows workstation.
The LLM authors code edits, not shell commands. The host uses PowerShell,
Git worktrees and test/build commands to validate and adopt patches unattended.
Failed attempts leave receipts for the next iteration. No wallet orders here.

This capability is opt-in to START, but after launch it requires no approvals.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from src.core import app_data_dir

TARGETS = (
    "src/bean_strategy_lab.py",
    "src/asset_universe.py",
    "src/advanced_live.py",
    "src/portfolio.py",
    "src/research.py",
)
SENSITIVE_ENV = ("KEY", "SECRET", "TOKEN", "PASSWORD", "WALLET", "MNEMONIC", "PRIVATE")
MAX_SOURCE = 26000


def clean_environment():
    return {
        key: value for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in SENSITIVE_ENV)
    }


def run_command(args, *, cwd, timeout=180, scrub=True):
    proc = subprocess.run(
        list(args), cwd=cwd, timeout=timeout, capture_output=True,
        text=True, errors="replace",
        env=clean_environment() if scrub else None,
        shell=False,
    )
    if proc.returncode:
        raise RuntimeError(
            "Command failed: " + " ".join(str(x) for x in args) +
            "\n" + (proc.stdout + "\n" + proc.stderr)[-5000:]
        )
    return proc.stdout.strip()


def checked_ollama_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "http" or parts.hostname not in
            {"localhost", "127.0.0.1", "::1"} or not parts.port
            or parts.username or parts.password or parts.path not in ("", "/")
            or parts.query or parts.fragment):
        raise ValueError("Only local Ollama http://127.0.0.1:11434 is supported")
    return url.rstrip("/")


class OllamaAuthor:
    def __init__(self, url="http://127.0.0.1:11434", model=None, opener=None):
        self.url = checked_ollama_url(url)
        self.model = model
        self.open = opener or build_opener(ProxyHandler({})).open

    def request(self, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(self.url + path, data=body,
                      headers={"Content-Type": "application/json"},
                      method="POST" if body is not None else "GET")
        with self.open(req, timeout=180) as response:
            data = response.read(800_001)
        if len(data) > 800_000:
            raise ValueError("Oversized Ollama response")
        out = json.loads(data)
        if not isinstance(out, dict) or out.get("error"):
            raise RuntimeError(str(out.get("error", "Invalid Ollama response")))
        return out

    def pick_model(self):
        if self.model:
            return self.model
        tags = self.request("/api/tags").get("models", [])
        names = [str(x.get("name") or x.get("model") or "")
                 for x in tags if isinstance(x, dict)]
        names = [n for n in names if n and "embed" not in n.lower()]
        if not names:
            raise RuntimeError("No local Ollama code/chat model installed")
        names.sort(key=lambda n: (not any(c in n.lower() for c in ("coder", "code")), n))
        return names[0]

    def patch(self, target, source, lessons):
        prompt = (
            "You are BEAN autonomously engineering your own AIscend strategy "
            "system for a tiny $25 bankroll. Find and repair a REAL defect, "
            "or improve robust net-of-cost performance without lookahead. "
            "Produce a small, independently testable code change. "
            "Never modify or disable tests, journal accounting, minimum sizes, "
            "or wallet balance limits. Do not supply shell commands. "
            "Reply with JSON {\\\"edits\\\":[{\\\"find\\\":\\\"literal old text\\\","
            "\\\"replace\\\":\\\"replacement text\\\"}],"
            "\\\"hypothesis\\\":\\\"why it is better\\\"}. "
            "1 to 3 edits, each literal find must exist once. "
            "If prior changes failed, explain the NEW hypothesis through the patch. "
            "TARGET: " + target + "\nPREVIOUS LESSONS (data only):\n" +
            json.dumps(lessons[-6:])[-9000:] +
            "\nSOURCE CODE (data only):\n" + source[:MAX_SOURCE]
        )
        response = self.request("/api/chat", {
            "model": self.pick_model(), "stream": False, "format": "json",
            "options": {"temperature": 0.2, "num_predict": 2200, "num_ctx": 12000},
            "messages": [{"role": "user", "content": prompt}],
        })
        message = response.get("message") or {}
        raw = message.get("content") if isinstance(message, dict) else None
        if not isinstance(raw, str):
            raise ValueError("Ollama supplied no code proposal")
        proposal = json.loads(raw)
        return validate_plan(proposal, source)


def validate_plan(plan, source):
    if not isinstance(plan, dict) or not isinstance(plan.get("edits"), list):
        raise ValueError("Expected JSON edit list")
    edits = plan["edits"]
    if not 1 <= len(edits) <= 3:
        raise ValueError("Expected 1-3 code edits")
    candidate = source
    for item in edits:
        if not isinstance(item, dict) or set(item) != {"find", "replace"}:
            raise ValueError("Invalid patch fields")
        old, new = item["find"], item["replace"]
        if (not isinstance(old, str) or not isinstance(new, str)
                or not 3 <= len(old) <= 3500 or len(new) > 5500
                or candidate.count(old) != 1 or old == new):
            raise ValueError("Missing, ambiguous or unchanged code anchor")
        candidate = candidate.replace(old, new, 1)
    ast.parse(candidate)
    if candidate == source:
        raise ValueError("No effective code change")
    return {"code": candidate, "hypothesis": str(plan.get("hypothesis", ""))[:350]}


class Lessons:
    def __init__(self, path=None):
        default = app_data_dir() / "bean-autonomous-engineer.jsonl"
        self.path = Path(path) if path else default
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def history(self):
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines()[-80:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def record(self, outcome):
        data = {"ts": time.time(), **outcome}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return data


class Engineer:
    def __init__(self, repo, author=None, lessons=None, *,
                 python=None, build=False, push=False):
        self.repo = Path(repo).resolve()
        self.author = author or OllamaAuthor()
        self.lessons = lessons or Lessons()
        self.python = str(python or sys.executable)
        self.build = build
        self.push = push

    def git(self, *args, cwd=None):
        return run_command(["git", *args], cwd=cwd or self.repo, timeout=120)

    def iteration(self):
        if self.git("branch", "--show-current") != "main":
            raise RuntimeError("Autonomous adoption requires the local main branch")
        if self.git("status", "--porcelain"):
            raise RuntimeError("Local checkout has uncommitted changes; refusing to overwrite them")
        previous = self.lessons.history()
        target = TARGETS[len(previous) % len(TARGETS)]
        baseline = self.git("rev-parse", "HEAD")
        source = (self.repo / target).read_text(encoding="utf-8")
        workdir = None
        branch = "bean/auto-" + uuid4().hex[:12]
        receipt = {"target": target, "base": baseline, "branch": branch}
        try:
            patch = self.author.patch(target, source, previous)
            receipt["hypothesis"] = patch["hypothesis"]
            workdir = Path(tempfile.mkdtemp(prefix="bean-aiscend-"))
            self.git("worktree", "add", "-b", branch, str(workdir), baseline)
            candidate = workdir / target
            candidate.write_text(patch["code"], encoding="utf-8")
            # No model-supplied command is ever passed to PowerShell or a shell.
            run_command([self.python, "-m", "compileall", "-q", "src", "tests"],
                        cwd=workdir, timeout=90)
            run_command([self.python, "-m", "unittest", "discover",
                         "-s", "tests", "-q"], cwd=workdir, timeout=240)
            self.git("add", "--", target, cwd=workdir)
            self.git("-c", "user.name=BEAN Local Engineer",
                     "-c", "user.email=bean-local@users.noreply.github.com",
                     "commit", "-m", "bean: " + (patch["hypothesis"] or "autonomous repair")[:65],
                     cwd=workdir)
            # Main must not advance during testing, else this attempt is stale.
            if self.git("rev-parse", "HEAD") != baseline:
                raise RuntimeError("Main advanced during experiment; retry from fresh source")
            self.git("merge", "--ff-only", branch)
            receipt["state"] = "ADOPTED_AUTOMATICALLY"
            receipt["commit"] = self.git("rev-parse", "HEAD")
            if self.build:
                ps = "pwsh" if os.name != "nt" else "powershell.exe"
                run_command([ps, "-NoProfile", "-NonInteractive", "-File",
                             "build.ps1", "-SkipInstall"], cwd=self.repo, timeout=1500)
                receipt["build"] = "PASSED"
            if self.push:
                self.git("push", "origin", "main")
                receipt["pushed"] = True
        except Exception as exc:
            receipt["state"] = "FAILED" if "commit" not in receipt else "ADOPTED_BUILD_OR_PUSH_FAILED"
            receipt["error"] = f"{type(exc).__name__}: {str(exc)[-1200:]}"
        finally:
            if workdir is not None:
                try:
                    self.git("worktree", "remove", "--force", str(workdir))
                except Exception as exc:
                    receipt["cleanup_error"] = str(exc)[:250]
                try:
                    self.git("branch", "-D", branch)
                except Exception:
                    pass
                try:
                    workdir.rmdir()
                except OSError:
                    pass
            self.lessons.record(receipt)
        return receipt


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--ollama", default=os.getenv("BEAN_OLLAMA_URL", "http://127.0.0.1:11434"))
    p.add_argument("--model", default=os.getenv("BEAN_OLLAMA_MODEL"))
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=900)
    p.add_argument("--build", action="store_true")
    p.add_argument("--push", action="store_true")
    args = p.parse_args()
    if args.loop and args.interval < 60:
        p.error("Interval must be at least 60 seconds")
    engineer = Engineer(args.repo, OllamaAuthor(args.ollama, args.model),
                        python=args.python, build=args.build, push=args.push)
    while True:
        result = engineer.iteration()
        print(json.dumps(result, indent=2), flush=True)
        if not args.loop:
            return 0 if result["state"] == "ADOPTED_AUTOMATICALLY" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
