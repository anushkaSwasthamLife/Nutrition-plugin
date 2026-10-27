"""Edge cases for the data (tampering, inconsistency, production switches) and for the HTTP interface."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dixit_answer import loader
from dixit_answer.loader import LoadError, load_library, load_manifest, load_swastham_data
from dixit_answer.pipeline import Engine
from dixit_answer.web import create_app
from edge_helpers import assert_valid

DATA = Path(__file__).resolve().parent.parent / "dixit_answer" / "data"


# ---------------------------------------------------------------- library tampering
@pytest.fixture(scope="module")
def library_copy(tmp_path_factory, settings):
    dst = tmp_path_factory.mktemp("lib")
    for rel in ("01_data/nutrition_indian_meals.jsonl", "01_data/nutrition_usda_reference.jsonl", "01_data/quarantined_indian_records.jsonl"):
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(settings.library_dir) / rel, dst / rel)
    return dst


def with_library(settings, path):
    s = copy.copy(settings)
    s.library_dir = path
    return s


def test_untouched_copy_loads(settings, library_copy):
    assert load_library(with_library(settings, library_copy)).report["eligible"]["usda"] == 13560


def test_one_changed_byte_stops_start(settings, library_copy, tmp_path):
    for rel in ("01_data/nutrition_indian_meals.jsonl", "01_data/nutrition_usda_reference.jsonl", "01_data/quarantined_indian_records.jsonl"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(library_copy / rel, tmp_path / rel)
    target = tmp_path / "01_data/nutrition_indian_meals.jsonl"
    data = bytearray(target.read_bytes())
    data[1000] ^= 1
    target.write_bytes(bytes(data))
    with pytest.raises(LoadError, match="Hash mismatch"):
        load_library(with_library(settings, tmp_path))


def test_missing_file_and_unknown_environment_are_load_errors(settings, tmp_path):
    with pytest.raises(LoadError, match="missing"):
        load_library(with_library(settings, tmp_path))
    bad = copy.copy(settings)
    bad.environment = "staging"
    with pytest.raises(LoadError, match="Unknown environment"):
        load_library(bad)


def test_wrong_counts_in_the_manifest_are_load_errors(settings):
    manifest = copy.deepcopy(load_manifest())
    manifest["library"]["files"]["indian"]["eligible_count"] += 1
    with pytest.raises(LoadError, match="eligible records"):
        load_library(settings, manifest)
    manifest = copy.deepcopy(load_manifest())
    manifest["library"]["files"]["quarantine"]["count"] += 1
    with pytest.raises(LoadError, match="Quarantine"):
        load_library(settings, manifest)


def test_quarantined_and_blocked_records_are_never_used_anywhere(engine):
    lib = engine.library
    quarantined = {json.loads(l)["nutrition_record_id"] for l in
                   (Path(engine.settings.library_dir) / "01_data/quarantined_indian_records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    assert not quarantined & set(lib.records)
    from dixit_answer.facts import make_fact
    for rid in list(lib.flags)[:300]:
        if "macro_energy_mismatch" in lib.flags[rid]:
            for f in ("energy_kcal", "fat_g", "carbohydrate_g"):
                assert make_fact(lib, rid, f) is None


# ---------------------------------------------------------------- the pinned data files are consistent with each other
def test_every_reference_between_the_data_files_resolves(engine):
    problems = []
    foods = engine.index.foods
    lib = engine.library
    for f in foods.values():
        for env in ("development", "production"):
            for rid in (f.get("fact_records") or {}).get(env, []):
                if env == "development" and rid not in lib.records:
                    problems.append(f"{f['id']} fact record {rid} not loaded")
        rid = f.get("ingredient_record")
        if rid and rid not in lib.records:
            problems.append(f"{f['id']} ingredient record {rid} not loaded")
        if rid and lib.is_indian(rid):
            problems.append(f"{f['id']} ingredient record {rid} is an Indian record (not allowed for recipe maths)")
        if f.get("generic") and f["generic"] not in foods:
            problems.append(f"{f['id']} generic {f['generic']} unknown")
    for recipe in engine.data.recipes["recipes"]:
        for item in recipe["ingredients"]:
            if item["food"] not in foods:
                problems.append(f"{recipe['recipe_id']} ingredient {item['food']} unknown")
        for fid in recipe["main_ingredients"]:
            if fid not in foods:
                problems.append(f"{recipe['recipe_id']} main {fid} unknown")
    for entry in engine.data.approved_list["entries"]:
        for fid in entry["foods"]:
            if fid not in foods:
                problems.append(f"approved list {entry['guidance_id']} food {fid} unknown")
    for allergen, entry in engine.data.allergen_map["allergens"].items():
        for fid in entry["foods"]:
            if fid not in foods:
                problems.append(f"allergen map {allergen} food {fid} unknown")
    for rule in engine.data.swap_table["repairs"]:
        for key in ("food", "to"):
            if rule.get(key) and rule[key] not in foods:
                problems.append(f"swap table {key} {rule[key]} unknown")
    for tip in engine.data.cooking_tips["tips"]:
        for env in ("development", "production"):
            for rid, _ in tip[env]["compare"]:
                if env == "development" and rid not in lib.records:
                    problems.append(f"tip {tip['tip_id']} record {rid} not loaded")
    group_foods = {fid for e in engine.data.approved_list["entries"] for fid in e["foods"]}
    assert not problems, problems
    assert group_foods


def test_alias_collisions_are_deterministic_and_reported(engine):
    seen = {}
    for food in engine.data.food_master["foods"]:
        for alias in [food["name"], *food.get("aliases", [])]:
            key = alias.lower().strip()
            if key in seen and seen[key] != food["id"]:
                pytest.fail(f"alias {alias!r} is on both {seen[key]} and {food['id']}")
            seen[key] = food["id"]


def test_every_recipe_in_the_collection_passes_its_own_gate_for_a_plain_profile(engine):
    from dixit_answer.person import Person
    svc = engine.recipes
    person = Person(allergies=[], conditions=[])
    limits = svc.compile_limits(person)
    for raw in engine.data.recipes["recipes"]:
        recipe = svc._from_collection(raw["recipe_id"])
        svc.calculate(recipe)
        gate = svc.gate(recipe, person, limits, set())
        assert not [r for r in gate.reasons if "steps mention" in r or "no nutrition data" in r or "isn't a home" in r], (raw["name"], gate.reasons)


def test_production_refuses_every_unsigned_data_file(settings):
    prod = copy.copy(settings)
    prod.environment = "production"
    with pytest.raises(LoadError):
        load_swastham_data(prod)
    prod_manifest = load_manifest()["environments"]["production"]
    assert prod_manifest["recipe_ai_drafting"] is False and prod_manifest["sources"] == ["usda"]
    assert not prod_manifest.get("indian_licence_id")


def test_every_unsigned_data_file_declares_its_status():
    for name in ("approved_list_test.json", "cooking_tips_test.json", "recipes_test.json", "condition_limits_test.json",
                 "swap_table_test.json", "food_master.json", "word_lists.json", "allergen_map.json", "golden_rules.json",
                 "rules_registry.json", "cooking_factors.json"):
        data = json.loads((DATA / name).read_text(encoding="utf-8"))
        assert str(data.get("status", "")).upper() in {"TEST ONLY", "PARTIAL", "PROPOSED", "STUB"}, name


# ---------------------------------------------------------------- HTTP interface
@pytest.fixture
def client(engine):
    return TestClient(create_app(engine))


def test_http_happy_path_and_static_page(client):
    assert client.get("/").status_code == 200 and "Nutrition Assistant" in client.get("/").text
    assert client.get("/api/info").json()["programme"]["identity"]["assistant_title"] == "Dixit Nutrition Assistant"   # named by the doctor's pack
    r = client.post("/api/chat", json={"message": "I have aloo and onion, what can I make?", "profile": {"allergies": []}})
    assert r.status_code == 200 and r.json()["type"] == "meal_from_ingredients"


@pytest.mark.parametrize("body", [
    "not json", b"\xff\xfe\x00", "[]", "null", "42", '{"message": 5}', '{"message": null}', '{"message": ["a"]}', "{}",
    '{"message": "hi", "conversation_id": 5}', '{"message": "hi", "conversation_id": "' + "a" * 65 + '"}',
    '{"message": "hi", "profile": "x"}', '{"message": "hi", "profile": [1]}', '{"message": "' + "a" * 2001 + '"}',
])
def test_malformed_requests_are_rejected_without_a_server_error(client, body):
    r = client.post("/api/chat", content=body if isinstance(body, bytes) else body, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422), (body[:40], r.status_code)


def test_message_at_the_http_limit_with_odd_content(client):
    r = client.post("/api/chat", json={"message": "😀" * 1000, "profile": {"allergies": []}})
    assert r.status_code == 200
    assert_valid(type("R", (), {"answer": r.json()["answer"], "checks": r.json()["checks"], "fallback": r.json()["fallback"], "turn": r.json()["turn"]})())


def test_profile_as_the_wrong_type_through_http_is_a_422_or_handled(client):
    for profile in ("string", [1, 2], 5):
        r = client.post("/api/chat", json={"message": "hello", "profile": profile})
        assert r.status_code in (200, 422)


def test_feedback_validation(client):
    ok = {"conversation_id": "c", "turn": 1, "kind": "helpful"}
    assert client.post("/api/feedback", json=ok).status_code == 200
    for bad in ({**ok, "kind": "bogus"}, {**ok, "turn": "x"}, {**ok, "kind": None}, {"turn": 1}, {**ok, "note": "x" * 501},
                {**ok, "conversation_id": "x" * 65}):
        assert client.post("/api/feedback", json=bad).status_code in (400, 422), bad


def test_unknown_routes_and_methods(client):
    assert client.get("/api/chat").status_code == 405
    assert client.get("/nope").status_code == 404
    assert client.post("/api/reset/does-not-exist").status_code == 200
    assert client.get("/api/info").status_code == 200


def test_audit_log_is_append_only_json_lines_and_never_holds_the_profile_secrets(client, engine):
    client.post("/api/chat", json={"message": "can I have paneer?", "profile": {"allergies": ["milk"], "secret_token": "abc123"}})
    text = engine.settings.audit_log.read_text(encoding="utf-8")
    for line in text.splitlines():
        json.loads(line)                                                  # every line is valid JSON
    assert "abc123" not in text
