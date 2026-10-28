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


def test_the_committed_manifest_carries_a_release_record_for_every_file_it_names():
    manifest = load_manifest()
    record = manifest["release"]
    listed = release.listed_files(manifest)
    assert set(record["files"]) == set(listed)
    assert set(manifest["swastham_data"]) <= set(listed) and "programme_rules.dixit_lifestyle" in listed
    for key, entry in record["files"].items():
        assert entry["name"] == listed[key] and len(entry["sha256"]) == 64
    assert "built_at" not in record and "release_id" not in record and "digest" not in record      # nothing in the record changes without a file


def test_the_release_id_comes_from_the_content_only():
    manifest = load_manifest()
    result = release.verify(manifest)
    assert result["release_id"] == "DIXIT-REL-" + release.digest_of(manifest["release"]["library_digest"], manifest["release"]["files"])[:8]
    assert len(result["release_id"]) == len("DIXIT-REL-") + 8


def test_rebuilding_with_no_change_leaves_the_manifest_byte_for_byte_the_same(data_copy):
    before = (data_copy / "manifest.json").read_bytes()
    release.build(data_copy / "manifest.json", data_copy)
    assert (data_copy / "manifest.json").read_bytes() == before


def test_a_changed_programme_rules_file_is_caught(data_copy):
    # Today the one programme's rules file is golden_rules.json, which is also in swastham_data. Give it its own file so the
    # programme_rules section is pinned on its own.
    shutil.copy(data_copy / "golden_rules.json", data_copy / "programme_extra.json")
    manifest = _manifest(data_copy)
    manifest["programme_rules"]["dixit_lifestyle"] = "programme_extra.json"
    (data_copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    release.build(data_copy / "manifest.json", data_copy)
    assert release.verify(_manifest(data_copy), data_copy)["verified"]
    path = data_copy / "programme_extra.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["note"] = "edited after the release record"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    result = release.verify(_manifest(data_copy), data_copy)
    assert not result["verified"] and result["drifted"] == ["programme_rules.dixit_lifestyle"]


def test_a_manifest_section_added_later_is_pinned_without_changing_the_code(data_copy):
    manifest = _manifest(data_copy)
    manifest["recipe_maps"] = {"book_a": "book_ingredient_map.json"}
    result = release.verify(manifest, data_copy)
    assert not result["verified"] and "recipe_maps.book_a" in result["drifted"]
    assert "named in the manifest but not in the release record" in result["problems"][0]
    (data_copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    release.build(data_copy / "manifest.json", data_copy)
    assert "recipe_maps.book_a" in _manifest(data_copy)["release"]["files"]


def test_every_recipe_book_map_is_pinned_and_a_changed_one_is_caught(data_copy):
    # PR #4 review: the maps sit in a list ("recipe_books"), not a {name: file.json} section, so they were not found by shape.
    manifest = load_manifest()
    for book in manifest["recipe_books"]:
        assert manifest["release"]["files"][f"recipe_books.{book['id']}"]["name"] == book["map"]
    book = _manifest(data_copy)["recipe_books"][0]
    path = data_copy / book["map"]
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["note"] = "edited after the release record"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    result = release.verify(_manifest(data_copy), data_copy)
    assert not result["verified"] and result["drifted"] == [f"recipe_books.{book['id']}"]


def test_every_file_named_by_the_manifest_is_in_the_record():
    manifest = load_manifest()
    named = set()
    for section, value in manifest.items():
        if isinstance(value, dict) and value and all(isinstance(v, str) and v.endswith(".json") for v in value.values()):
            named |= set(value.values())
    named |= {book["map"] for book in manifest.get("recipe_books") or []}
    assert named == {entry["name"] for entry in manifest["release"]["files"].values()}


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
    assert len(set(problems)) == 1 and f"was {old}" in problems[0]      # golden_rules.json is named twice (data and programme rules)


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
    assert any("no longer named" in p for p in release.verify(dropped, data_copy)["problems"])


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
    first = release.verify(_manifest(data_copy), data_copy)["release_id"]
    path = data_copy / "hinglish_glossary.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["note"] = str(doc.get("note", "")) + " edited"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    release.build(data_copy / "manifest.json", data_copy)
    second = release.verify(_manifest(data_copy), data_copy)
    assert second["release_id"] != first and second["verified"]


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
    for name in release.listed_files(_manifest(folder)).values():
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
    assert last["release"]["id"] == engine.versions["release"] == release.verify(load_manifest())["release_id"]
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
