"""Run every Python unittest module in its own process.

A long-lived headless Qt interpreter can segfault in later, unrelated threaded
tests during native Qt cleanup. Isolating modules preserves full test coverage
and makes a native crash fail the *specific* module rather than corrupting the
rest of the suite. This is not a test skip or an xfail.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def main() -> int:
    files = sorted(TESTS.glob("test_*.py"))
    if not files:
        print("FAIL: no test modules found", flush=True)
        return 2
    failures = []
    start = time.monotonic()
    for i, path in enumerate(files, 1):
        print(f"\n=== Python suite {i}/{len(files)}: {path.name} ===",
              flush=True)
        command = [sys.executable, "-m", "unittest", "discover",
                   "-s", "tests", "-p", path.name, "-v"]
        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env["PYTHONFAULTHANDLER"] = "1"
        try:
            outcome = subprocess.run(command, cwd=ROOT, env=env,
                                     timeout=150, check=False)
            if outcome.returncode:
                failures.append((path.name, f"exit={outcome.returncode}"))
        except subprocess.TimeoutExpired:
            failures.append((path.name, "timeout=150s"))
        if failures:
            print(f"FAIL: {path.name}: {failures[-1][1]}", flush=True)
    print(f"\nPython desktop suite: {len(files)} modules in "
          f"{time.monotonic()-start:.1f}s; failures={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
