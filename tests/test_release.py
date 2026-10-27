"""Release record (release.py): one ID, a SHA-256 for every data file, checked at start-up."""
import copy
import json
import shutil

import pytest

from dixit_answer import loader, release
from dixit_answer.config import DATA_DIR
from dixit_answer.loader import LoadError, load_manifest, load_swastham_data


@pytest.fixture
def data_copy(tmp_path):
    """A private copy of the data folder with a fresh release record."""
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    release.build(target / "manifest.json", target)
    return target


def _manifest(folder):
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


def test_the_committed_manifest_carries_a_release_record_for_every_data_file():
    manifest = load_manifest()
    record = manifest["release"]
    assert record["release_id"].startswith("DIXIT-REL-") and record["release_id"].endswith(record["digest"][:8])
    assert set(record["files"]) == set(manifest["swastham_data"])
    for key, entry in record["files"].items():
        assert entry["name"] == manifest["swastham_data"][key] and len(entry["sha256"]) == 64


def test_a_fresh_record_verifies(data_copy):
    result = release.verify(_manifest(data_copy), data_copy)
    assert result["verified"] and not result["problems"] and not result["drifted"]


def test_a_changed_file_is_named_even_when_its_version_label_is_the_same(data_copy):
    path = data_copy / "emergency_rules.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["negation_window_words"] = 9
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    result = release.verify(_manifest(data_copy), data_copy)
    assert not result["verified"] and result["drifted"] == ["emergency_rules"]
    assert "same version label" in result["problems"][0]


def test_a_new_version_label_is_reported_as_such(data_copy):
    path = data_copy / "golden_rules.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    old = doc["version"]
    doc["version"] = old + "-next"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    problems = release.verify(_manifest(data_copy), data_copy)["problems"]
    assert len(problems) == 1 and f"was {old}" in problems[0]


def test_line_endings_do_not_change_the_hash(data_copy):
    path = data_copy / "word_lists.json"
    text = path.read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(text.replace(b"\n", b"\r\n"))
    assert release.verify(_manifest(data_copy), data_copy)["verified"]
    path.write_bytes(text)
    assert release.verify(_manifest(data_copy), data_copy)["verified"]


def test_a_missing_or_unreadable_file_is_a_problem_not_a_crash(data_copy):
    (data_copy / "swap_table_test.json").unlink()
    (data_copy / "dish_rules.json").write_text("{not json", encoding="utf-8")
    result = release.verify(_manifest(data_copy), data_copy)
    assert not result["verified"] and set(result["drifted"]) == {"swap_table", "dish_rules"}


def test_a_file_added_to_or_dropped_from_the_manifest_is_a_problem(data_copy):
    manifest = _manifest(data_copy)
    added = copy.deepcopy(manifest)
    added["swastham_data"]["extra"] = "extra.json"
    assert any("not in the release record" in p for p in release.verify(added, data_copy)["problems"])
    dropped = copy.deepcopy(manifest)
    del dropped["swastham_data"]["dish_rules"]
    assert any("no longer listed" in p for p in release.verify(dropped, data_copy)["problems"])


def test_changed_library_pins_change_the_digest(data_copy):
    manifest = _manifest(data_copy)
    manifest["library"]["files"]["usda"]["sha256"] = "0" * 64
    result = release.verify(manifest, data_copy)
    assert not result["verified"] and "library pins" in result["problems"][0]


def test_no_release_record_is_a_problem():
    manifest = copy.deepcopy(load_manifest())
    del manifest["release"]
    result = release.verify(manifest)
    assert not result["verified"] and result["release_id"] is None and "no release record" in result["problems"][0]


def test_rebuilding_after_a_change_gives_a_new_id(data_copy):
    first = _manifest(data_copy)["release"]["release_id"]
    path = data_copy / "hinglish_glossary.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["note"] = str(doc.get("note", "")) + " edited"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    release.build(data_copy / "manifest.json", data_copy)
    second = _manifest(data_copy)["release"]
    assert second["release_id"] != first and release.verify(_manifest(data_copy), data_copy)["verified"]


def test_development_loads_a_drifted_set_and_reports_it(data_copy, settings, monkeypatch):
    path = data_copy / "white_lists.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["note"] = "edited"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", data_copy)
    data = load_swastham_data(settings, _manifest(data_copy))
    assert data.release["verified"] is False and data.release["drifted"] == ["white_lists"]


def _sign_everything(folder):
    """Mark every file APPROVED and rebuild the record, so only the release check is left to refuse."""
    for name in _manifest(folder)["swastham_data"].values():
        doc = json.loads((folder / name).read_text(encoding="utf-8"))
        doc["status"] = "APPROVED"
        (folder / name).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    release.build(folder / "manifest.json", folder)


def test_production_loads_a_signed_release_that_matches(data_copy, settings, monkeypatch):
    _sign_everything(data_copy)
    monkeypatch.setattr(loader, "DATA_DIR", data_copy)
    prod = copy.copy(settings)
    prod.environment = "production"
    data = load_swastham_data(prod, _manifest(data_copy))
    assert data.release["verified"] and data.release["release_id"].startswith("DIXIT-REL-")


def test_production_refuses_signed_files_that_changed_after_the_record(data_copy, settings, monkeypatch):
    _sign_everything(data_copy)
    path = data_copy / "condition_limits_test.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["note"] = "changed after sign-off"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", data_copy)
    prod = copy.copy(settings)
    prod.environment = "production"
    with pytest.raises(LoadError, match="not the signed release.*condition_limits_test.json"):
        load_swastham_data(prod, _manifest(data_copy))


def test_production_refuses_signed_files_with_no_release_record(data_copy, settings, monkeypatch):
    _sign_everything(data_copy)
    manifest = _manifest(data_copy)
    del manifest["release"]
    monkeypatch.setattr(loader, "DATA_DIR", data_copy)
    prod = copy.copy(settings)
    prod.environment = "production"
    with pytest.raises(LoadError, match="no release record"):
        load_swastham_data(prod, manifest)


def test_every_answer_is_audited_with_its_release(engine, chat):
    chat("what is a good lunch with dal")
    last = [json.loads(line) for line in engine.settings.audit_log.read_text(encoding="utf-8").splitlines()][-1]
    assert last["release"]["id"] == engine.versions["release"] == load_manifest()["release"]["release_id"]
    assert last["release"]["verified"] is True and last["release"]["drifted"] == []
    assert last["versions"]["release"] == last["release"]["id"]


def test_readiness_reports_a_release_that_no_longer_matches(data_copy):
    from dixit_answer.readiness import check
    path = data_copy / "dish_rules.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["note"] = "edited"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    items = [i for i in check(data_copy / "manifest.json", data_copy)["items"] if i["area"] == "release"]
    assert len(items) == 1 and "dish_rules.json" in items[0]["problem"]
