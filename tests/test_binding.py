"""The programme binding (Dixit nutrition binding package, Knee-style handover): the package is hash-pinned, the effective runtime
is named and checked against the combinations the package validated, production refuses anything else, the package's structural
tests run (and say "not run" when they cannot), and every ID it declares is mapped to where this pipeline implements it. The tests
that read the package or the pinned library skip when they are not on this machine."""
import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from dixit_answer import readiness
from dixit_answer.binding import (FAILED, NOT_RUN, PASSED, BindingError, coverage, effective_runtime, load_binding, map_problems,
                                  merge_steps, open_binding, structural_results)
from dixit_answer.config import DATA_DIR, PROJECT_DIR, binding_path
from dixit_answer.loader import LoadError, load_manifest

MANIFEST = load_manifest()
ENTRY = MANIFEST["programme_binding"]
BINDING_DIR = binding_path({})
PINNED_LIBRARY = (PROJECT_DIR / "nutrition" / "Swastham_Nutrition_Data_Library_v1_0_3" / "Swastham_Nutrition_Data_Library_v1_0_3")
MAP = json.loads((DATA_DIR / "binding_map.json").read_text(encoding="utf-8"))
needs_binding = pytest.mark.skipif(BINDING_DIR is None, reason="the binding package is not on this machine")
needs_pinned_library = pytest.mark.skipif(not PINNED_LIBRARY.is_dir(), reason="library v1.0.3 is not on this machine")


@pytest.fixture(scope="module")
def binding():
    if BINDING_DIR is None:
        pytest.skip("the binding package is not on this machine")
    return load_binding(BINDING_DIR, ENTRY)


@pytest.fixture
def package_copy(tmp_path):
    if BINDING_DIR is None:
        pytest.skip("the binding package is not on this machine")
    return Path(shutil.copytree(BINDING_DIR, tmp_path / "package"))


# ---------------------------------------------------------------- the package and its pin
def test_the_map_and_the_manifest_name_the_same_binding():
    assert MAP["binding_id"] == ENTRY["id"] and MAP["status"] == "PROPOSED"


def test_the_binding_is_found_off_or_by_its_variable(tmp_path):
    assert binding_path({ENTRY["env"]: "off"}) is None
    assert binding_path({ENTRY["env"]: str(tmp_path)}) == tmp_path


@needs_binding
def test_the_package_loads_and_declares_what_the_map_covers(binding):
    assert binding.binding_id == "DIXIT-NUTRITION-1.2.6" and not binding.production_enabled
    assert len(binding.rules) == 11 and len(binding.checks) == 14 and len(binding.subintents) == 8
    assert map_problems(binding, MAP) == []


def test_a_changed_package_file_is_refused(package_copy):
    rules = package_copy / ENTRY["files"]["rules"]
    rules.write_text(rules.read_text(encoding="utf-8").replace('"priority": 1003', '"priority": 1010'), encoding="utf-8")
    with pytest.raises(BindingError, match="does not match its release hash"):
        load_binding(package_copy, ENTRY)


def test_a_changed_release_file_is_refused(package_copy):
    release = package_copy / ENTRY["release_manifest"]
    release.write_text(release.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BindingError, match="not the pinned release"):
        load_binding(package_copy, ENTRY)


def test_a_missing_package_is_refused(tmp_path):
    with pytest.raises(BindingError, match="not found"):
        load_binding(tmp_path / "nowhere", ENTRY)


# ---------------------------------------------------------------- the effective runtime and production
@needs_binding
def test_the_effective_runtime_names_what_this_machine_has(binding):
    runtime = effective_runtime(binding, MANIFEST)
    assert runtime["runtime_id"] == f"DIXIT-RT-none+OVRnone+NUT1.2.6+SNDL{MANIFEST['library']['version']}"
    assert not runtime["validated"] and any("dixit_baseline" in m for m in runtime["mismatches"])


@needs_binding
def test_only_the_validated_combination_is_validated(binding):
    entry = {**ENTRY, "runtime": {"dixit_baseline": "1.1", "required_overlay": "1.1.1"}}
    full = dataclasses.replace(binding, entry=entry)
    assert effective_runtime(full, {**MANIFEST, "library": {"version": "1.0.3"}}) == {
        "runtime_id": "DIXIT-RT-1.1+OVR1.1.1+NUT1.2.6+SNDL1.0.3", "validated": True, "mismatches": []}
    assert not effective_runtime(full, {**MANIFEST, "library": {"version": "1.0.5"}})["validated"]


@needs_binding
def test_development_records_the_binding_problems_and_carries_on(settings):
    found, status = open_binding(dataclasses.replace(settings, binding_dir=BINDING_DIR), MANIFEST)
    assert found is not None and status["state"] == "loaded"
    assert "the binding package has production_enabled false" in status["problems"]


@needs_binding
def test_production_refuses_an_unvalidated_binding(settings):
    with pytest.raises(LoadError, match="Production refuses the programme binding"):
        open_binding(dataclasses.replace(settings, binding_dir=BINDING_DIR, environment="production"), MANIFEST)


def test_production_refuses_a_missing_binding(settings):
    with pytest.raises(LoadError, match="not on this machine"):
        open_binding(dataclasses.replace(settings, binding_dir=None, environment="production"), MANIFEST)
    _, status = open_binding(dataclasses.replace(settings, binding_dir=None), MANIFEST)
    assert status["state"] == "off"


def test_production_refuses_a_changed_binding(settings, package_copy):
    (package_copy / ENTRY["files"]["intents"]).write_text("[]", encoding="utf-8")
    with pytest.raises(LoadError, match="does not match its release"):
        open_binding(dataclasses.replace(settings, binding_dir=package_copy, environment="production"), MANIFEST)
    _, status = open_binding(dataclasses.replace(settings, binding_dir=package_copy), MANIFEST)
    assert status["state"] == "invalid"


def test_every_answer_records_the_binding_and_the_effective_runtime(engine):
    assert engine.versions["binding"].startswith(ENTRY["id"] + ", ")
    assert engine.versions["binding_map"] == MAP["version"]
    assert engine.versions["effective_runtime"] == (engine.binding_status["runtime_id"] or "none")


def test_readiness_lists_the_binding_problems():
    items = [i for i in readiness.check()["items"] if i["area"] == "binding"]
    assert items, "a binding that production would refuse must show in the readiness report"


# ---------------------------------------------------------------- orchestration merge policy
ANCHORS = ["A", "B", "C"]


def test_steps_merge_by_anchor_rank_binding_and_id_whatever_the_order():
    one = [{"patch_step_id": "N-2", "after_anchor": "B", "insertion_rank": 20, "binding_id": "NUT"},
           {"patch_step_id": "N-1", "after_anchor": "B", "insertion_rank": 10, "binding_id": "NUT"},
           {"patch_step_id": "N-0", "before_anchor": "B", "insertion_rank": 10, "binding_id": "NUT"}]
    two = [{"patch_step_id": "X-1", "after_anchor": "B", "insertion_rank": 10, "binding_id": "OTHER"}]
    assert merge_steps(ANCHORS, one, two) == merge_steps(ANCHORS, two, one) == ["N-0", "N-1", "X-1", "N-2"]
    assert merge_steps(ANCHORS, one, one) == merge_steps(ANCHORS, one)


def test_a_step_at_an_unknown_anchor_fails_deployment():
    with pytest.raises(BindingError, match="unknown anchor"):
        merge_steps(ANCHORS, [{"patch_step_id": "N-1", "after_anchor": "Z", "insertion_rank": 10, "binding_id": "NUT"}])


# ---------------------------------------------------------------- structural acceptance tests
@needs_binding
def test_structural_tests_that_need_no_library_pass_and_baseline_ones_are_not_run(binding):
    results = {r["id"]: r for r in structural_results(binding, None)}
    assert set(results) == {f"STR-{n:03d}" for n in range(1, 16)}
    for test_id in ("STR-001", "STR-002", "STR-003", "STR-004", "STR-005", "STR-007", "STR-009", "STR-010", "STR-012", "STR-015"):
        assert results[test_id]["status"] == PASSED, results[test_id]
    for test_id in ("STR-006", "STR-011", "STR-008", "STR-013", "STR-014"):      # no baseline, no library: never a pass
        assert results[test_id]["status"] == NOT_RUN, results[test_id]


@needs_binding
@needs_pinned_library
def test_the_pinned_library_passes_its_structural_tests(binding):
    results = {r["id"]: r["status"] for r in structural_results(binding, PINNED_LIBRARY)}
    assert results["STR-008"] == results["STR-013"] == results["STR-014"] == PASSED


@needs_binding
def test_a_library_that_is_not_the_pinned_one_fails(binding, tmp_path):
    data = tmp_path / "01_data"
    data.mkdir()
    for pin in binding.parts["library_registration"]["pinned_files"]:
        (tmp_path / pin["path"]).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / pin["path"]).write_text("", encoding="utf-8")
    assert {r["id"]: r["status"] for r in structural_results(binding, tmp_path)}["STR-008"] == FAILED


# ---------------------------------------------------------------- coverage
@needs_binding
def test_coverage_counts_every_declared_id_once(binding):
    report = coverage(binding, MAP)
    for kind, result in report.items():
        assert result["unmapped"] == [] and sum(result["by_status"].values()) == result["total"], kind


@needs_binding
def test_an_unmapped_or_unknown_id_is_a_map_problem(binding):
    broken = json.loads(json.dumps(MAP))
    del broken["rules"]["NUT-ROUTE-001"]
    broken["checks"]["NUT-NOT-A-CHECK"] = {"status": "implemented", "where": "-"}
    broken["anchors"]["ANCHOR_LOGGING"]["status"] = "done"
    problems = map_problems(binding, broken)
    assert "rules: NUT-ROUTE-001 is not mapped" in problems
    assert any("NUT-NOT-A-CHECK is not in binding" in p for p in problems)
    assert any("ANCHOR_LOGGING has status 'done'" in p for p in problems)


# ---------------------------------------------------------------- binding check NUT-FIELD-001: excluded values are never given
METRIC_REFUSAL = "Insulin demand, glycaemic index and glycaemic load aren't part of the approved nutrition data"


@pytest.mark.parametrize("message", ["Calculate insulin demand for this meal.", "what is the GI of rice", "food insulin index of dal",
                                     "rice ka GI kya hai"])
def test_an_excluded_value_is_declined_and_never_estimated(engine, message):
    r = engine.answer(message, None, {"allergies": [], "diet_type": "vegetarian"})
    assert r.answer.startswith(METRIC_REFUSAL)
    assert "doesn't ban foods" not in r.answer and not any(ch.isdigit() for ch in r.answer)


def test_a_fact_asked_with_an_excluded_value_is_still_answered(engine):
    r = engine.answer("calories and glycemic index of dal", None, {"allergies": [], "diet_type": "vegetarian"})
    assert r.answer.startswith(METRIC_REFUSAL) and "calories" in r.answer.split(" instead.", 1)[1]


def test_a_food_named_with_an_excluded_value_gets_the_decline_first(engine):
    r = engine.answer("glycaemic load of chapati", None, {"allergies": [], "diet_type": "vegetarian"})
    first = r.answer.splitlines()[0]
    assert first.startswith(METRIC_REFUSAL) and not any(ch.isdigit() for ch in first)


@pytest.mark.parametrize("message", ["I have a GI problem, can I eat dal", "what is gil", "is rice good for insulin resistance"])
def test_near_misses_are_not_declined(engine, message):
    assert METRIC_REFUSAL not in engine.answer(message, None, {"allergies": [], "diet_type": "vegetarian"}).answer
