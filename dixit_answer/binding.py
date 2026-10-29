"""The programme binding: the signed package that says how this nutrition plugin plugs into the Dixit runtime.

The package (manifest "programme_binding", kept outside git) is the nutrition counterpart of the Knee handover: deterministic rules
with registered priorities, the answer checks the final validator must run, the declared intent vocabulary, the orchestration
anchors and where nutrition steps go, the conflict registry, and the tests that prove an implementation. Its own release file
lists every file's SHA-256; the manifest pins that release file, so any edit to the package stops it being used.

This module reads the package, works out the effective runtime ("DIXIT-RT-<baseline>+OVR<overlay>+NUT<binding>+SNDL<library>")
and whether that combination is one the package validated, runs the structural acceptance tests it defines, and reports how much
of it this pipeline implements, using `data/binding_map.json`. Production refuses to start without a valid binding, on an
unvalidated combination, or while the package itself says production is off. Development records the same problems instead.

Run:  .venv\\Scripts\\python.exe -m dixit_answer.binding [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import DATA_DIR, Settings, load_settings
from .loader import LoadError, load_manifest, sha256_of

PASSED, FAILED, NOT_RUN = "passed", "failed", "not_run"
COVERAGE_STATUSES = ("implemented", "partial", "missing", "not_applicable")
SYNTHETIC_BINDING = "ZZ-SYNTHETIC-BINDING-0.0"   # STR-005: a made-up second binding, never part of any runtime


class BindingError(LoadError):
    """The binding package is missing a file, has been changed since it was pinned, or contradicts itself."""


@dataclass
class Binding:
    path: Path
    entry: dict                                  # the manifest's "programme_binding"
    parts: dict = field(default_factory=dict)    # file key (entry "files") -> its JSON

    @property
    def package(self) -> dict:
        return self.parts["package_manifest"]

    @property
    def binding_id(self) -> str:
        return self.package.get("binding_id", "")

    @property
    def version(self) -> str:
        return str(self.package.get("version", ""))

    @property
    def production_enabled(self) -> bool:
        return self.package.get("production_enabled") is True

    @property
    def validated_combination(self) -> dict:
        return (self.package.get("compatibility_matrix") or {}).get("validated_combination") or {}

    @property
    def rules(self) -> dict[str, dict]:
        """Every rule the binding adds or replaces, by ID: the nutrition rules, the universal rules and the patched overlay rules."""
        out: dict[str, dict] = {}
        for key, list_key in (("rules", "rules"), ("universal_rules", "rules"), ("patch_rules", "replace_rules")):
            for rule in self.parts[key].get(list_key) or []:
                out[rule["rule_id"]] = rule
        return out

    @property
    def checks(self) -> dict[str, dict]:
        return {c["check_id"]: c for c in self.parts["answer_checks"].get("checks") or []}

    @property
    def intents(self) -> dict[str, dict]:
        return {i["intent_id"]: i for i in self.parts["intents"]}

    @property
    def subintents(self) -> set[str]:
        return {i for i, item in self.intents.items() if item.get("taxonomy_level") == "nutrition_subintent"}

    @property
    def anchors(self) -> list[str]:
        return [step["anchor_id"] for step in sorted(self.parts["flow"]["steps"], key=lambda s: s["order"])]

    @property
    def patch_steps(self) -> list[dict]:
        return list(self.parts["orchestration_patch"].get("insertions") or [])

    @property
    def conflicts(self) -> list[dict]:
        return list(self.parts["conflicts"].get("entries") or [])

    @property
    def tests(self) -> dict[str, list]:
        """{"functional": [...], "structural": [...], "critical": [recheck names]}"""
        return {"functional": list(self.parts["functional_tests"].get("tests") or []),
                "structural": list(self.parts["structural_tests"].get("tests") or []),
                "critical": list(self.parts["critical_rechecks"].get("critical_rechecks") or [])}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BindingError(f"binding file {path.name} cannot be read ({exc.__class__.__name__})") from exc


def release_problems(path: Path, entry: dict) -> list[str]:
    """STR-010: the release file is the one pinned in the manifest, and every file it lists is present with that hash."""
    release = path / entry["release_manifest"]
    if not release.is_file():
        return [f"{entry['release_manifest']} is missing"]
    if sha256_of(release) != entry["release_manifest_sha256"]:
        return [f"{entry['release_manifest']} is not the pinned release: the package changed since it was pinned"]
    problems = []
    for name, info in (_read_json(release).get("files") or {}).items():
        target = path / name
        if not target.is_file():
            problems.append(f"{name} is missing")
        elif sha256_of(target) != info.get("sha256"):
            problems.append(f"{name} does not match its release hash")
    return problems


def load_binding(path: Path, entry: dict | None = None) -> Binding:
    """The binding package at `path`, after its release hashes are checked. Raises BindingError on any mismatch."""
    entry = entry or load_manifest().get("programme_binding")
    if not entry:
        raise BindingError("the manifest has no programme_binding")
    path = Path(path)
    if not path.is_dir():
        raise BindingError(f"binding package not found: {path}")
    problems = release_problems(path, entry)
    if problems:
        raise BindingError("binding package does not match its release: " + "; ".join(problems[:5]))
    binding = Binding(path=path, entry=entry, parts={key: _read_json(path / name) for key, name in entry["files"].items()})
    if binding.binding_id != entry["id"]:
        raise BindingError(f"binding package is {binding.binding_id!r}, the manifest expects {entry['id']!r}")
    return binding


def effective_runtime(binding: Binding, manifest: dict) -> dict:
    """{"runtime_id", "validated", "mismatches"}: the runtime this pipeline would form with the binding, and whether the package
    lists that exact combination as validated. A package or library this machine does not have is "none", never assumed."""
    have = {"dixit_baseline": binding.entry.get("runtime", {}).get("dixit_baseline"),
            "required_overlay": binding.entry.get("runtime", {}).get("required_overlay"),
            "nutrition_binding": binding.version,
            "nutrition_library": str((manifest.get("library") or {}).get("version") or "") or None}
    runtime_id = (f"DIXIT-RT-{have['dixit_baseline'] or 'none'}+OVR{have['required_overlay'] or 'none'}"
                  f"+NUT{have['nutrition_binding']}+SNDL{have['nutrition_library'] or 'none'}")
    wanted = binding.validated_combination
    mismatches = [f"{key}: have {have.get(key) or 'none'}, validated {value}" for key, value in wanted.items()
                  if str(have.get(key) or "") != str(value)]
    if not wanted:
        mismatches.append("the package declares no validated combination")
    return {"runtime_id": runtime_id, "validated": not mismatches, "mismatches": mismatches}


def open_binding(settings: Settings, manifest: dict) -> tuple[Binding | None, dict]:
    """The binding for the engine, with a status for the audit record: {"state", "runtime_id", "problems"}.

    Production refuses (LoadError) when the binding is missing or invalid, the combination is not validated, or the package says
    production is off. Development carries on and records the same problems, because the binding does not yet change answers.
    """
    entry = manifest.get("programme_binding")
    if not entry:
        status = {"state": "not configured", "runtime_id": None, "problems": ["the manifest has no programme_binding"]}
        binding = None
    elif settings.binding_dir is None:
        status = {"state": "off", "runtime_id": None, "problems": [f"binding package {entry['id']} is not on this machine"]}
        binding = None
    else:
        try:
            binding = load_binding(settings.binding_dir, entry)
        except BindingError as exc:
            binding, status = None, {"state": "invalid", "runtime_id": None, "problems": [str(exc)]}
        else:
            runtime = effective_runtime(binding, manifest)
            problems = list(runtime["mismatches"])
            if not binding.production_enabled:
                problems.append("the binding package has production_enabled false")
            status = {"state": "validated" if not problems else "loaded", "runtime_id": runtime["runtime_id"], "problems": problems}
    if settings.environment == "production" and status["problems"]:
        raise LoadError("Production refuses the programme binding: " + "; ".join(status["problems"]))
    return binding, status


# ---------------------------------------------------------------- orchestration merge (DIXIT-BINDING-MERGE-POLICY-1.0)
def merge_steps(anchors: list[str], *patches: list[dict]) -> list[str]:
    """The patch step IDs in effective order: sorted by anchor position, insertion rank, binding ID, then step ID. A step sits just
    after its after_anchor or just before its before_anchor. Applying a step twice is a no-op; an unknown anchor is an error."""
    position = {anchor: i for i, anchor in enumerate(anchors)}
    steps: dict[str, tuple] = {}
    for patch in patches:
        for step in patch:
            anchor = step.get("after_anchor") or step.get("before_anchor")
            if anchor not in position:
                raise BindingError(f"patch step {step.get('patch_step_id')} names unknown anchor {anchor!r}")
            place = position[anchor] + (0.5 if step.get("after_anchor") else -0.5)
            steps[step["patch_step_id"]] = (place, step.get("insertion_rank", 0), step.get("binding_id", ""), step["patch_step_id"])
    return [key[3] for key in sorted(steps.values())]


# ---------------------------------------------------------------- structural acceptance tests (STR-001 .. STR-015)
def _jsonl_ids(path: Path, key: str = "nutrition_record_id") -> list:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line).get(key) for line in handle if line.strip()]


def _str_001(b, _lib):
    bad = [s["patch_step_id"] for s in b.patch_steps if isinstance(s.get("after_order"), int) or isinstance(s.get("before_order"), int)]
    return (FAILED, f"integer order references: {bad}") if bad else (PASSED, f"{len(b.patch_steps)} steps use anchors only")


def _str_002(b, _lib):
    unknown = [s["patch_step_id"] for s in b.patch_steps if (s.get("after_anchor") or s.get("before_anchor")) not in b.anchors]
    return (FAILED, f"unknown anchors in {unknown}") if unknown else (PASSED, f"all anchors exist in the {len(b.anchors)}-step flow")


def _str_003(b, _lib):
    ids = [s["patch_step_id"] for s in b.patch_steps]
    slots = [(s.get("after_anchor"), s.get("before_anchor"), s.get("insertion_rank"), s.get("binding_id")) for s in b.patch_steps]
    if len(ids) != len(set(ids)):
        return FAILED, "duplicate patch_step_id"
    if len(slots) != len(set(slots)):
        return FAILED, "two steps of one binding share an anchor and insertion rank"
    return PASSED, "step IDs unique; ranks unique within each anchor"


def _str_004(b, _lib):
    once, twice = merge_steps(b.anchors, b.patch_steps), merge_steps(b.anchors, b.patch_steps, b.patch_steps)
    return (PASSED, "second application is a no-op") if once == twice else (FAILED, "applying the patch twice changed the flow")


def _str_005(b, _lib):
    anchor = "ANCHOR_RETRIEVAL_FILTERS_APPLIED"
    ours = [s for s in b.patch_steps if s.get("after_anchor") == anchor]
    if not ours:
        return NOT_RUN, f"the binding inserts nothing after {anchor}"
    other = [{"patch_step_id": f"SYN-{r}", "after_anchor": anchor, "insertion_rank": r, "binding_id": SYNTHETIC_BINDING}
             for r in (10, 20, 30)]
    first, second = merge_steps(b.anchors, b.patch_steps, other), merge_steps(b.anchors, other, b.patch_steps)
    if first != second:
        return FAILED, "the effective order depends on which binding is applied first"
    mine = [step for step in first if step in {s["patch_step_id"] for s in ours}]
    expected = [s["patch_step_id"] for s in sorted(ours, key=lambda s: s["insertion_rank"])]
    if mine != expected:
        return FAILED, "a second binding changed the relative order of the nutrition steps"
    return PASSED, "order is independent of application order; nutrition ranks keep their relative order"


def _priorities(b) -> dict[str, int]:
    return {rid: rule["priority"] for rid, rule in b.rules.items() if isinstance(rule.get("priority"), int)}


def _str_006(b, _lib):
    prios = _priorities(b)
    clashes = sorted({p for p in prios.values() if list(prios.values()).count(p) > 1})
    if clashes:
        return FAILED, f"priorities used twice inside the binding: {clashes}"
    return NOT_RUN, (f"{len(prios)} binding priorities are unique among themselves; the Dixit baseline v1.1 and overlay v1.1.1 "
                     "are not on this machine, so uniqueness across the merged runtime is not checked")


def _str_007(b, _lib):
    ok = bool(b.package.get("effective_runtime_id")) and bool(b.validated_combination)
    return (PASSED, b.package["effective_runtime_id"]) if ok else (FAILED, "effective_runtime_id or compatibility_matrix missing")


def _str_008(b, lib):
    reg = b.parts["library_registration"]
    if lib is None or not Path(lib).is_dir():
        return NOT_RUN, "no nutrition library folder on this machine"
    problems = []
    for pin in reg.get("pinned_files") or []:
        target = Path(lib) / pin["path"]
        if not target.is_file():
            problems.append(f"{pin['path']} missing")
            continue
        if sha256_of(target) != pin["sha256"]:
            problems.append(f"{pin['path']} hash differs")
        if "record_count" in pin and len(_jsonl_ids(target)) != pin["record_count"]:
            problems.append(f"{pin['path']} record count differs")
    if problems:
        return FAILED, f"library at {Path(lib).name} is not the pinned v{reg.get('required_version')}: " + "; ".join(problems[:4])
    return PASSED, f"all {len(reg.get('pinned_files') or [])} pinned library files match v{reg.get('required_version')}"


def _str_009(b, _lib):
    entries = b.conflicts
    ids = [e.get("conflict_id") for e in entries]
    if not entries or len(ids) != len(set(ids)) or any(not e.get("resolution_action") for e in entries):
        return FAILED, "conflict registry addendum is empty, has duplicate IDs or an entry without a resolution"
    if b.parts["conflicts"].get("authoritative_mechanism") != "conflict_supersession_registry":
        return FAILED, "the addendum does not declare the conflict_supersession_registry as the one mechanism"
    return PASSED, f"{len(entries)} conflicts declared in one registry addendum"


def _str_010(b, _lib):
    problems = release_problems(b.path, b.entry)
    return (FAILED, "; ".join(problems[:5])) if problems else (PASSED, "every package file matches its release hash")


def _str_011(_b, _lib):
    return NOT_RUN, "needs the Dixit baseline v1.1 and overlay v1.1.1 rule registries, which are not on this machine"


def _str_012(b, _lib):
    direction = b.parts["priority_registry"].get("precedence_direction")
    return (PASSED, direction) if direction == "higher_priority_integer_wins" else (FAILED, f"precedence_direction is {direction!r}")


def _str_013(b, lib):
    if lib is None or not Path(lib).is_dir():
        return NOT_RUN, "no nutrition library folder on this machine"
    indian = Path(lib) / "01_data" / "nutrition_indian_meals.jsonl"
    with open(indian, encoding="utf-8") as handle:
        bases = [((json.loads(line).get("data_quality") or {}).get("energy_basis")) for line in handle if line.strip()]
    counts = {basis: bases.count(basis) for basis in set(bases)}
    expected = {"source_published": 728, "source_declared_unreliable": 137}
    if counts != expected:
        return FAILED, f"energy_basis counts are {counts}, expected {expected}"
    return PASSED, f"{len(bases)} Indian records, energy_basis {counts}"


def _str_014(b, lib):
    if lib is None or not Path(lib).is_dir():
        return NOT_RUN, "no nutrition library folder on this machine"
    lib = Path(lib)
    quarantined = {q for q in _jsonl_ids(lib / "01_data" / "quarantined_indian_records.jsonl") if q}
    leaked = quarantined & set(_jsonl_ids(lib / "01_data" / "nutrition_indian_meals.jsonl"))
    rule_ok = "NUT-DATA-ELIGIBILITY-001" in b.rules and "NUT-QUAR-001" in b.checks
    if leaked or not rule_ok:
        return FAILED, f"{len(leaked)} quarantined records in runtime data; rule and check present: {rule_ok}"
    return PASSED, f"{len(quarantined)} quarantined records absent from runtime data; excluded by rule and check"


def _str_015(b, _lib):
    classifier = next((s for s in b.patch_steps if s.get("classifier_output_field") == "nutrition_subintent"), None)
    if classifier is None:
        return FAILED, "no patch step declares the nutrition_subintent classifier"
    declared = set(classifier.get("allowed_output_values") or [])
    patch = next((r for r in b.parts["patch_rules"].get("replace_rules") or [] if r.get("rule_id") == "PATCH-MOD-001"), {})
    used = set((patch.get("triggers") or {}).get("intent_and_topic", {}).get("intents_any") or [])
    used |= set((patch.get("triggers") or {}).get("exclude_intents_any") or [])
    if declared != b.subintents or not used <= b.subintents:
        return FAILED, f"classifier values {sorted(declared ^ b.subintents)} or PATCH-MOD-001 values {sorted(used - b.subintents)} undeclared"
    return PASSED, f"{len(declared)} sub-intents declared and used consistently"


STRUCTURAL = {"STR-001": _str_001, "STR-002": _str_002, "STR-003": _str_003, "STR-004": _str_004, "STR-005": _str_005,
              "STR-006": _str_006, "STR-007": _str_007, "STR-008": _str_008, "STR-009": _str_009, "STR-010": _str_010,
              "STR-011": _str_011, "STR-012": _str_012, "STR-013": _str_013, "STR-014": _str_014, "STR-015": _str_015}


def structural_results(binding: Binding, library_dir: Path | None) -> list[dict]:
    """Every structural test the package defines: {"id", "assertion", "status", "detail"}. A test with no check written here is
    "not_run", never passed."""
    results = []
    for test in binding.tests["structural"]:
        check = STRUCTURAL.get(test["test_id"])
        status, detail = check(binding, library_dir) if check else (NOT_RUN, "no check is written for this test yet")
        results.append({"id": test["test_id"], "assertion": test.get("assertion", ""), "status": status, "detail": detail})
    return results


# ---------------------------------------------------------------- coverage: how much of the binding this pipeline implements
COVERAGE_KINDS = {"rules": lambda b: list(b.rules), "checks": lambda b: list(b.checks), "intents": lambda b: list(b.intents),
                  "anchors": lambda b: b.anchors, "patch_steps": lambda b: [s["patch_step_id"] for s in b.patch_steps],
                  "conflicts": lambda b: [c["conflict_id"] for c in b.conflicts],
                  "functional_tests": lambda b: [t["test_id"] for t in b.tests["functional"]],
                  "critical_rechecks": lambda b: b.tests["critical"]}


def coverage(binding: Binding, binding_map: dict) -> dict:
    """{kind: {"total", "by_status", "unmapped", "items": [{"id", "status", "where", "note"}]}} for every ID the binding declares.
    An ID the map does not mention is "unmapped": a new binding version must be mapped before it is counted."""
    out = {}
    for kind, ids_of in COVERAGE_KINDS.items():
        mapped = binding_map.get(kind) or {}
        items, unmapped = [], []
        for item_id in ids_of(binding):
            entry = mapped.get(item_id)
            if entry is None:
                unmapped.append(item_id)
                continue
            items.append({"id": item_id, **entry})
        by_status = {s: sum(1 for i in items if i.get("status") == s) for s in COVERAGE_STATUSES}
        out[kind] = {"total": len(ids_of(binding)), "by_status": by_status, "unmapped": unmapped, "items": items}
    return out


def map_problems(binding: Binding, binding_map: dict) -> list[str]:
    """What is wrong with the map itself: an unknown status, an ID the binding does not declare, or a declared ID left out."""
    problems = []
    for kind, ids_of in COVERAGE_KINDS.items():
        declared = set(ids_of(binding))
        for item_id, entry in (binding_map.get(kind) or {}).items():
            if item_id not in declared:
                problems.append(f"{kind}: {item_id} is not in binding {binding.binding_id}")
            if entry.get("status") not in COVERAGE_STATUSES:
                problems.append(f"{kind}: {item_id} has status {entry.get('status')!r}")
        problems += [f"{kind}: {item_id} is not mapped" for item_id in sorted(declared - set(binding_map.get(kind) or {}))]
    return problems


# ---------------------------------------------------------------- report
def report(settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    manifest = load_manifest()
    binding_map = json.loads((DATA_DIR / manifest["swastham_data"]["binding_map"]).read_text(encoding="utf-8"))
    binding, status = open_binding(replace(settings, environment="development"), manifest)   # report, never refuse
    if binding is None:
        return {"status": status, "structural": [], "coverage": {}, "map_problems": []}
    return {"status": status, "binding": binding.binding_id, "production_enabled": binding.production_enabled,
            "structural": structural_results(binding, settings.library_dir), "coverage": coverage(binding, binding_map),
            "map_problems": map_problems(binding, binding_map)}


def render(result: dict) -> str:
    status = result["status"]
    lines = [f"Programme binding: {result.get('binding') or '-'}  ({status['state']})",
             f"Effective runtime: {status.get('runtime_id') or '-'}"]
    lines += [f"  ! {p}" for p in status["problems"]]
    if result["structural"]:
        counts = {s: sum(1 for r in result["structural"] if r["status"] == s) for s in (PASSED, FAILED, NOT_RUN)}
        lines += ["", f"Structural tests: {counts[PASSED]} passed, {counts[FAILED]} failed, {counts[NOT_RUN]} not run"]
        lines += [f"  {r['status']:<8} {r['id']}  {r['detail']}" for r in result["structural"]]
    if result["coverage"]:
        lines += ["", "Coverage (implemented / partial / missing / not applicable of total):"]
        for kind, c in result["coverage"].items():
            s = c["by_status"]
            lines.append(f"  {kind:<18} {s['implemented']:>2} / {s['partial']:>2} / {s['missing']:>2} / {s['not_applicable']:>2} of "
                         f"{c['total']:>2}" + (f"   unmapped: {', '.join(c['unmapped'])}" if c["unmapped"] else ""))
    lines += [f"  map problem: {p}" for p in result["map_problems"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check the programme binding package and how much of it this pipeline implements.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    result = report()
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render(result))
    failed = any(r["status"] == FAILED for r in result["structural"]) or result["map_problems"] or result["status"]["state"] == "invalid"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
