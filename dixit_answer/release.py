"""Release record: a SHA-256 for every data file the loader reads from the manifest, kept in `data/manifest.json` under "release".

Why: the audit log used to store the version label of each file. A label can stay the same while the file changes. The release
record pins the actual bytes, so any answer can be traced to the exact files that produced it, and production can refuse a set
of files that is not the one that was signed.

    .venv\\Scripts\\python.exe -m dixit_answer.release build      # write the record (after the files are reviewed)
    .venv\\Scripts\\python.exe -m dixit_answer.release verify     # compare the files with the record (exit 1 on any difference)

Which files are pinned: every manifest section that maps names to .json files (`swastham_data`, `programme_rules`, and any registry
added later), found by shape, so a new section cannot be forgotten. A file in `swastham_data` is recorded under its key; a file in
another section under "<section>.<key>".

The record holds no date and no stored ID, so rebuilding with no change leaves the manifest byte for byte the same, and two PRs that
change different data files do not touch the same lines. The release ID is worked out from the record: DIXIT-REL-<first 8 of the digest>.

Development only reports a difference (the files are edited all day). Production refuses to start on one (loader.py).
Hashes ignore line endings (CRLF and LF give the same value), so a file checked out on Windows and on Linux matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .config import DATA_DIR

VERSION_KEYS = ("version", "list_version", "collection_version")
MANIFEST_NAME = "manifest.json"
NOT_FILE_SECTIONS = {"library", "environments", "release"}
HASH_RULE = "SHA-256 of the file with line endings made LF"


def hash_bytes(raw: bytes) -> str:
    """SHA-256 of file content with CRLF and CR turned into LF first."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def file_hash(path: Path) -> str:
    return hash_bytes(path.read_bytes())


def file_version(data) -> str:
    if not isinstance(data, dict):
        return ""
    for key in VERSION_KEYS:
        if key in data:
            return str(data[key])
    return ""


def listed_files(manifest: dict) -> dict[str, str]:
    """Every file the manifest names, as {record key: file name}. Found by shape, not by a fixed list of sections."""
    out: dict[str, str] = {}
    for section, value in manifest.items():
        if section in NOT_FILE_SECTIONS or not isinstance(value, dict) or not value:
            continue
        if not all(isinstance(v, str) and v.endswith(".json") for v in value.values()):
            continue
        for key, name in value.items():
            out[key if section == "swastham_data" else f"{section}.{key}"] = name
    # A recipe book's ingredient map sits in a list ("recipe_books": [{"id": ..., "map": "book_ingredient_map.json"}]); the
    # recipes themselves live outside the repo and are not pinned here.
    for book in manifest.get("recipe_books") or []:
        out[f"recipe_books.{book['id']}"] = book["map"]
    return out


def library_digest(manifest: dict) -> str:
    """One hash over the pinned library files."""
    parts = [f"library:{manifest['library']['version']}"]
    parts += [f"library.{key}:{pin['sha256']}" for key, pin in sorted(manifest["library"]["files"].items())]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def digest_of(lib_digest: str, files: dict) -> str:
    parts = [f"library:{lib_digest}"] + [f"{key}:{entry['sha256']}" for key, entry in sorted(files.items())]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def release_id(digest: str) -> str:
    return "DIXIT-REL-" + digest[:8]


def _read(name: str, data_dir: Path, contents: dict | None) -> bytes:
    if contents is not None and name in contents:
        return contents[name]
    return (data_dir / name).read_bytes()


def compute(manifest: dict, data_dir: Path = DATA_DIR, contents: dict | None = None) -> dict:
    """The record the current files would produce."""
    files: dict[str, dict] = {}
    for key, name in sorted(listed_files(manifest).items()):
        raw = _read(name, data_dir, contents)
        data = json.loads(raw.decode("utf-8"))
        files[key] = {"name": name, "version": file_version(data), "status": str(data.get("status", "")) if isinstance(data, dict) else "",
                      "sha256": hash_bytes(raw)}
    return {"hash_rule": HASH_RULE, "library_digest": library_digest(manifest), "files": files}


def build(manifest_path: Path | None = None, data_dir: Path = DATA_DIR) -> dict:
    """Write the release record into the manifest. Returns {'release_id', 'files'}."""
    manifest_path = manifest_path or data_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("release", None)
    record = compute(manifest, data_dir)
    manifest["release"] = record
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"release_id": release_id(digest_of(record["library_digest"], record["files"])), "files": record["files"]}


def verify(manifest: dict, data_dir: Path = DATA_DIR, contents: dict | None = None) -> dict:
    """Compare the files with the release record. Pass `contents` ({file name: bytes}) to check the bytes already read.

    {"release_id", "verified": bool, "drifted": [record keys], "problems": [text]}"""
    record = manifest.get("release")
    if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
        return {"release_id": None, "verified": False, "drifted": [],
                "problems": ["the manifest has no release record (run: python -m dixit_answer.release build)"]}
    problems: list[str] = []
    drifted: list[str] = []
    listed = listed_files(manifest)
    for key in sorted(set(listed) - set(record["files"])):
        problems.append(f"{listed[key]}: named in the manifest but not in the release record")
        drifted.append(key)
    for key in sorted(set(record["files"]) - set(listed)):
        problems.append(f"{key}: in the release record but no longer named in the manifest")
        drifted.append(key)
    for key, entry in sorted(record["files"].items()):
        if key not in listed:
            continue
        if listed[key] != entry.get("name"):
            problems.append(f"{key}: the manifest now names {listed[key]}, the release record names {entry.get('name')}")
            drifted.append(key)
            continue
        try:
            raw = _read(entry["name"], data_dir, contents)
            version = file_version(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError) as exc:
            problems.append(f"{entry['name']}: cannot be read ({exc.__class__.__name__})")
            drifted.append(key)
            continue
        if hash_bytes(raw) != entry.get("sha256"):
            now = f" (version {version}, was {entry.get('version')})" if version != entry.get("version") else " (same version label)"
            problems.append(f"{entry['name']}: changed since the release record{now}")
            drifted.append(key)
    if record.get("library_digest") != library_digest(manifest):
        problems.append("the library pins in the manifest changed since the release record")
    return {"release_id": release_id(digest_of(str(record.get("library_digest")), record["files"])),
            "verified": not problems, "drifted": sorted(set(drifted)), "problems": problems}


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
        print(f"Release record written: {record['release_id']} ({len(record['files'])} files)")
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
