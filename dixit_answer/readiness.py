"""Production readiness report (plan item F): what is still unsigned, placeholder or test-only.

Run:  .venv\\Scripts\\python.exe -m dixit_answer.readiness [--json]

Exit code 0 only when every data file is signed, every limit has a source, reviewer and review date, and the production switches
in the manifest are consistent. It changes nothing; it reads the same files the chat reads.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import DATA_DIR
from .sign_off import _unfilled, file_problems, limit_problems

MANIFEST_PATH = DATA_DIR / "manifest.json"
# data files that carry an approver and a date once signed
APPROVAL_FIELDS = {"approved_list": ("approved_by", "approved_on"), "recipes": ("approved_by", "approved_on")}


def _read(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"cannot be read ({exc.__class__.__name__})"
    if not isinstance(data, dict):
        return None, "is not a JSON object"
    return data, None


def check(manifest_path: Path = MANIFEST_PATH, data_dir: Path = DATA_DIR) -> dict:
    """{'ready': bool, 'items': [{'area', 'file', 'problem'}], 'files': {key: status}}"""
    items: list[dict] = []
    files: dict[str, str] = {}

    def add(area: str, file: str, problem: str):
        items.append({"area": area, "file": file, "problem": problem})

    manifest, error = _read(manifest_path)
    if error:
        add("manifest", manifest_path.name, error)
        return {"ready": False, "items": items, "files": files}

    names = manifest.get("swastham_data")
    if not isinstance(names, dict) or not names:
        add("manifest", manifest_path.name, "lists no data files")
        names = {}
    for key, name in names.items():
        data, error = _read(data_dir / str(name))
        if error:
            add(key, str(name), error)
            files[key] = "unreadable"
            continue
        files[key] = str(data.get("status", "")) or "missing"
        for problem in file_problems(str(name), data):
            add(key, str(name), problem)
        for field in APPROVAL_FIELDS.get(key, ()):
            if _unfilled(data.get(field)):
                add(key, str(name), f"{field} is empty")
        if key == "condition_limits":
            for problem in limit_problems(data):
                add(key, str(name), problem)

    production = (manifest.get("environments") or {}).get("production")
    if not isinstance(production, dict):
        add("manifest", manifest_path.name, "has no production environment")
    else:
        if production.get("allow_test_only_data"):
            add("manifest", manifest_path.name, "production allows test-only data")
        if "indian" in (production.get("sources") or []) and _unfilled(production.get("indian_licence_id")):
            add("manifest", manifest_path.name, "production uses Indian records without a licence ID")
        if production.get("recipe_ai_drafting"):
            add("manifest", manifest_path.name, "AI recipe drafting is on in production before the 100-recipe dietitian review (item G)")
    return {"ready": not items, "items": items, "files": files}


def render(result: dict) -> str:
    lines = ["Production readiness: " + ("READY" if result["ready"] else f"NOT READY ({len(result['items'])} open points)"), ""]
    for key, status in result["files"].items():
        lines.append(f"  {key:<17} {status}")
    if result["items"]:
        lines += ["", "Open points:"]
        lines += [f"  - [{item['area']}] {item['problem']}" for item in result["items"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Report what is still unsigned before production.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")   # a Windows console may not show every character a reviewer typed
    except (AttributeError, ValueError):
        pass
    result = check()
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render(result))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
