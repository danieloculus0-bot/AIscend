"""Offline tests + read-only authenticated Coinbase relaunch gate.

Usage, from the *installed* AIscend checkout:
  python -m scripts.relaunch_check --expected-sha <reviewed-commit-sha>

No orders, transfers, cancellations, or execution threads are started.
A green check is not a profit forecast or confirmation of a real fill.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from scripts.live_preflight import inspect_advanced


def verify_installation(*, expected_sha: str, root: Path | None = None,
                        runner=None, inspector=None) -> dict[str, Any]:
    """Fail closed unless the reviewed local code, offline suite and account pass."""
    workdir = (root or Path(__file__).resolve().parents[1]).resolve()
    run = runner or subprocess.run
    inspect = inspector or inspect_advanced
    result: dict[str, Any] = {
        "read_only": True, "placed_orders": False,
        "local_checkout": str(workdir),
        "expected_sha": str(expected_sha).strip().lower(),
        "revision_verified": False, "unit_tests_passed": False,
        "coinbase_preflight_passed": False, "release_check_passed": False,
        "blockers": [], "warnings": [],
    }

    try:
        revision = run(["git", "rev-parse", "HEAD"], cwd=workdir,
                       capture_output=True, text=True, timeout=15,
                       check=False)
        actual = str(revision.stdout).strip().lower()
        if revision.returncode or len(actual) != 40 or any(
                c not in "0123456789abcdef" for c in actual):
            result["blockers"].append("LOCAL_GIT_REVISION_UNAVAILABLE")
            return result
        result["actual_sha"] = actual
        required = result["expected_sha"]
        if len(required) < 12 or len(required) > 40 or any(
                c not in "0123456789abcdef" for c in required
        ) or not actual.startswith(required):
            result["blockers"].append("LOCAL_CODE_DIFFERS_FROM_REVIEWED_RELEASE")
            return result
        state = run(["git", "status", "--porcelain", "--untracked-files=normal"],
                    cwd=workdir, capture_output=True, text=True,
                    timeout=15, check=False)
        if state.returncode:
            result["blockers"].append("LOCAL_WORKTREE_STATUS_FAILED")
            return result
        # Include untracked, non-ignored files: a stray Python module can
        # shadow reviewed code while git rev-parse still reports the right SHA.
        if state.stdout.strip():
            result["blockers"].append("LOCAL_SOURCE_HAS_UNCOMMITTED_CHANGES")
            return result
        result["revision_verified"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        result["blockers"].append("LOCAL_GIT_VERIFICATION_ERROR")
        result["warnings"].append(type(exc).__name__)
        return result

    try:
        test = run([sys.executable, "-m", "unittest", "discover", "-s",
                    "tests", "-q"], cwd=workdir, capture_output=True,
                   text=True, timeout=240, check=False)
        if test.returncode != 0:
            result["blockers"].append("LOCAL_PYTHON_REGRESSION_TESTS_FAILED")
            result["test_summary"] = (str(test.stdout) + "\n" +
                                      str(test.stderr))[-1200:]
            return result
        result["unit_tests_passed"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        result["blockers"].append("LOCAL_PYTHON_TEST_RUN_ERROR")
        result["warnings"].append(type(exc).__name__)
        return result

    try:
        # Enforce the exact same history and identity gate as Qt START.
        # Never report local release readiness from a weaker connection check.
        preflight = inspect(require_history=True)
        result["coinbase_preflight_passed"] = bool(
            preflight.get("execution_ready"))
        result["coinbase_preflight_blockers"] = list(
            preflight.get("blockers") or [])
        result["coinbase_preflight_warnings"] = list(
            preflight.get("warnings") or [])
        # Never repeat detailed credentials, client IDs, or portfolio amounts.
        if not result["coinbase_preflight_passed"]:
            result["blockers"].append("AUTHENTICATED_READ_ONLY_PREFLIGHT_BLOCKED")
    except Exception as exc:
        result["blockers"].append("AUTHENTICATED_PREFLIGHT_ERROR")
        result["warnings"].append(type(exc).__name__)
    result["release_check_passed"] = not result["blockers"]
    result["note"] = ("Local code/test/read-only Coinbase gates only; no live orders, "
                      "live fills, unattended runtime, or profitable outcomes proven.")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True,
                        help="Reviewed 12–40 digit Git revision from the tested PR")
    parser.add_argument("--output", type=Path,
                        help="Optional sanitized report JSON path")
    args = parser.parse_args(argv)
    report = verify_installation(expected_sha=args.expected_sha)
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if report["release_check_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
