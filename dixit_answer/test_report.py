"""Test reports per level: "defined", "run" and "passed" are never merged.

    .venv\\Scripts\\python.exe -m dixit_answer.test_report run        # run the whole suite and record the result as a development run
    .venv\\Scripts\\python.exe -m dixit_answer.test_report summary    # one line per level: PASS / FAIL / PARTIAL / NOT_RUN

Levels, in order of how much they prove:
  defined          the tests exist (the count from the newest run's collection; says nothing about behaviour)
  static           the data files match the signed release record (release.py), computed now
  development_run  the whole suite against the local server with the MOCK model (no AI is called)
  staging_run      the same suite on staging with the real model (set DIXIT_TEST_LEVEL=staging_run there)
  soft_launch      read by hand from the daily audit report; nothing writes this level automatically

A report is written only when DIXIT_TEST_REPORT is set (the `run` command sets it), so ordinary test runs leave nothing behind.
A run of some of the tests is recorded as scope "partial" and is never shown as PASS.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import release
from .config import DATA_DIR, PROJECT_DIR

LEVELS = ("defined", "static", "development_run", "staging_run", "soft_launch")
RUN_LEVELS = ("development_run", "staging_run")
NOTES = {
    "development_run": "ran with the mock model: no AI was called, so this does not prove the real model's wording passes",
    "staging_run": "ran on staging",
}


def report_dir() -> Path:
    return Path(os.environ.get("DIXIT_VAR_DIR") or PROJECT_DIR / "var") / "test_reports"


def scope_of(args: list[str]) -> str:
    """'full' when pytest was pointed at the whole tests folder (or nothing), 'partial' for a file, test id, -k or -m."""
    takes_value = {"-p", "-c", "-o", "-W", "--rootdir", "--tb", "--durations", "--maxfail", "--junitxml", "--basetemp"}
    positional, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in takes_value:
            skip = True                       # "-p no:cacheprovider": the value is not a test path
        elif not a.startswith("-"):
            positional.append(a)
    if any(a.split("=")[0] in {"-k", "-m", "--lf", "--sw"} for a in args):
        return "partial"
    if any(a.startswith("--deselect") for a in args):
        return "partial"
    for a in positional:
        name = a.replace("\\", "/").rstrip("/")
        if name not in {"tests", "./tests", ".", ""} and not name.endswith("/tests"):
            return "partial"
    return "full"


def write_report(environment: str, level: str, stats: dict, args: list[str], started: float, collected: int = 0,
                 directory: Path | None = None, manifest: dict | None = None) -> Path:
    directory = directory or report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    manifest = manifest or json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    checked = release.verify(manifest)
    failed = [r.nodeid for key in ("failed", "error") for r in stats.get(key, []) if hasattr(r, "nodeid")]
    counts = {k: len([r for r in stats.get(k, []) if getattr(r, "when", "call") in ("call", "setup", "teardown")]) for k in ("passed", "failed", "skipped", "error")}
    record = {
        "level": level, "environment": environment, "scope": scope_of(args), "args": args,
        "collected": collected, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - started, 1),
        "counts": counts, "failed": sorted(set(failed))[:50],
        "result": "PASS" if not counts["failed"] and not counts["error"] and counts["passed"] else "FAIL",
        "release_id": checked["release_id"], "release_verified": checked["verified"], "note": NOTES.get(level, ""),
    }
    path = directory / f"{time.strftime('%Y%m%dT%H%M%S')}_{level}_{environment}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def latest(directory: Path | None = None) -> dict[str, dict]:
    """The newest report per level."""
    directory = directory or report_dir()
    found: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("level") in LEVELS:
            found[rec["level"]] = rec
    return found


def summary(directory: Path | None = None, manifest: dict | None = None) -> list[dict]:
    """One row per level: {'level', 'status', 'detail'}. Status is PASS, FAIL, PARTIAL or NOT_RUN, never a guess."""
    manifest = manifest or json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    reports = latest(directory)
    current = release.verify(manifest)["release_id"]
    rows = []
    for level in LEVELS:
        rep = reports.get(level)
        if level == "defined":
            newest = max(reports.values(), key=lambda r: r["at"], default=None)
            rows.append({"level": level, "status": "DEFINED" if newest else "NOT_RUN",
                         "detail": f"{newest['collected']} tests collected (defined, not proof of behaviour)" if newest else "no run has been recorded"})
        elif level == "static":
            checked = release.verify(manifest)
            rows.append({"level": level, "status": "PASS" if checked["verified"] else "FAIL",
                         "detail": f"release {checked['release_id']}" if checked["verified"] else "; ".join(checked["problems"][:3])})
        elif rep is None:
            rows.append({"level": level, "status": "NOT_RUN", "detail": "no report"})
        else:
            c = rep["counts"]
            status = rep["result"] if rep["scope"] == "full" else "PARTIAL"
            stale = " (on an older release)" if rep.get("release_id") != current else ""
            rows.append({"level": level, "status": status,
                         "detail": f"{c['passed']} passed, {c['failed']} failed, {c['skipped']} skipped, {rep['environment']}, {rep['at']}{stale}"})
    return rows


def render(rows: list[dict]) -> str:
    width = max(len(r["level"]) for r in rows)
    return "\n".join(f"  {r['level']:<{width}}  {r['status']:<8}  {r['detail']}" for r in rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the suite and record it, or show the latest result per level.")
    parser.add_argument("action", choices=["run", "summary"])
    parser.add_argument("--level", default=os.environ.get("DIXIT_TEST_LEVEL", "development_run"), choices=RUN_LEVELS)
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.action == "run":
        env = dict(os.environ, DIXIT_TEST_REPORT="1", DIXIT_TEST_LEVEL=args.level)
        code = subprocess.call([sys.executable, "-m", "pytest", "-q", "tests"], cwd=PROJECT_DIR, env=env)
        print("\nTest levels:\n" + render(summary()))
        return code
    print("Test levels:\n" + render(summary()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
