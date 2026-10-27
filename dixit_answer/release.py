"""Release record: one ID, every data-file version and a SHA-256 for each, kept in `data/manifest.json` under "release".

Why: the audit log used to store the version label of each file. A label can stay the same while the file changes. The release
record pins the actual bytes, so any answer can be traced to the exact files that produced it, and production can refuse a set
of files that is not the one that was signed.

    .venv\\Scripts\\python.exe -m dixit_answer.release build      # write the record (after the files are reviewed)
    .venv\\Scripts\\python.exe -m dixit_answer.release verify     # compare the files with the record (exit 1 on any difference)

Development only reports a difference (the files are edited all day). Production refuses to start on one (loader.py).
Hashes ignore line endings (CRLF and LF give the same value), so a file checked out on Windows and on Linux matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from .config import DATA_DIR

VERSION_KEYS = ("version", "list_version", "collection_version")
MANIFEST_NAME = "manifest.json"


def file_hash(path: Path) -> str:
    """SHA-256 of a file with CRLF and CR turned into LF first."""
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def file_version(data) -> str:
    if not isinstance(data, dict):
        return ""
    for key in VERSION_KEYS:
        if key in data:
            return str(data[key])
    return ""


def digest_of(manifest: dict, files: dict) -> str:
    """One hash over the pinned library files and every Swastham data file, in a fixed order."""
    parts = [f"library:{manifest['library']['version']}"]
    for key, pin in sorted(manifest["library"]["files"].items()):
        parts.append(f"library.{key}:{pin['sha256']}")
    for key, entry in sorted(files.items()):
        parts.append(f"{key}:{entry['sha256']}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def compute(manifest: dict, data_dir: Path = DATA_DIR) -> dict:
    """The release block the current files would produce (no ID or build time yet)."""
    files: dict[str, dict] = {}
    for key, name in sorted(manifest["swastham_data"].items()):
        path = data_dir / name
        data = json.loads(path.read_text(encoding="utf-8"))
        files[key] = {"name": name, "version": file_version(data), "status": str(data.get("status", "")),
                      "sha256": file_hash(path)}
    return {"files": files, "digest": digest_of(manifest, files)}


def release_id(digest: str, when: time.struct_time | None = None) -> str:
    return "DIXIT-REL-" + time.strftime("%Y%m%d", when or time.localtime()) + "-" + digest[:8]


def build(manifest_path: Path | None = None, data_dir: Path = DATA_DIR) -> dict:
    """Write the release record into the manifest. Returns the record."""
    manifest_path = manifest_path or data_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("release", None)
    block = compute(manifest, data_dir)
    record = {"release_id": release_id(block["digest"]), "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "hash_rule": "SHA-256 of the file with line endings made LF", **block}
    manifest["release"] = record
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def verify(manifest: dict, data_dir: Path = DATA_DIR) -> dict:
    """Compare the files on disk with the release record.

    {"release_id", "digest", "verified": bool, "drifted": [file keys], "problems": [text]}"""
    record = manifest.get("release")
    if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
        return {"release_id": None, "digest": None, "verified": False, "drifted": [],
                "problems": ["the manifest has no release record (run: python -m dixit_answer.release build)"]}
    problems: list[str] = []
    drifted: list[str] = []
    listed = manifest.get("swastham_data") or {}
    for key in sorted(set(listed) - set(record["files"])):
        problems.append(f"{key}: listed in the manifest but not in the release record")
        drifted.append(key)
    for key in sorted(set(record["files"]) - set(listed)):
        problems.append(f"{key}: in the release record but no longer listed in the manifest")
        drifted.append(key)
    actual: dict[str, dict] = {}
    for key, entry in sorted(record["files"].items()):
        if key not in listed:
            continue
        if listed[key] != entry.get("name"):
            problems.append(f"{key}: the manifest now names {listed[key]}, the release record names {entry.get('name')}")
            drifted.append(key)
            continue
        path = data_dir / entry["name"]
        try:
            sha = file_hash(path)
            version = file_version(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            problems.append(f"{entry['name']}: cannot be read ({exc.__class__.__name__})")
            drifted.append(key)
            continue
        actual[key] = {"sha256": sha}
        if sha != entry.get("sha256"):
            now = f" (version {version}, was {entry.get('version')})" if version != entry.get("version") else " (same version label)"
            problems.append(f"{entry['name']}: changed since the release record{now}")
            drifted.append(key)
    if not problems and digest_of(manifest, record["files"]) != record.get("digest"):
        problems.append("the library pins in the manifest changed since the release record")
    return {"release_id": record.get("release_id"), "digest": record.get("digest"), "verified": not problems,
            "drifted": sorted(set(drifted)), "problems": problems}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify the release record in data/manifest.json.")
    parser.add_argument("action", choices=["build", "verify"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.action == "build":
        record = build()
        print(f"Release record written: {record['release_id']} ({len(record['files'])} files, digest {record['digest'][:12]}…)")
        return 0
    result = verify(json.loads((DATA_DIR / MANIFEST_NAME).read_text(encoding="utf-8")))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif result["verified"]:
        print(f"Release {result['release_id']}: every file matches the record.")
    else:
        print(f"Release {result['release_id'] or '(none)'}: {len(result['problems'])} difference(s)")
        for problem in result["problems"]:
            print("  - " + problem)
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
