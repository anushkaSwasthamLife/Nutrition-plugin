"""White lists and black lists: shipped data is consistent, and every rule is enforced against hostile or broken input."""
import copy
import json
from pathlib import Path

import pytest

from dixit_answer import lists
from dixit_answer.foods import LibrarySearch
from dixit_answer.loader import Library
from dixit_answer.person import AllergyFilter, Person
from dixit_answer.readiness import check

DATA = Path(__file__).resolve().parent.parent / "dixit_answer" / "data"
GOOD_DRAFT = {"name": "Lauki Tomato Sabji",
              "ingredients": [{"food_id": f, "grams": g} for f, g in
                              [("FOOD-LAUKI", 250), ("FOOD-TOMATO", 100), ("FOOD-OIL", 9), ("FOOD-JEERA", 2), ("FOOD-SALT", 3)]],
              "steps": ["Heat the oil and add the jeera.", "Add the lauki and tomato with salt and cook for 10 minutes till soft."]}


def problems(engine, environment="development", mutate=None):
    data = copy.deepcopy(engine.data)
    if mutate:
        mutate(data)
    return lists.list_problems(data, engine.library, engine.index, environment)


# ---------------------------------------------------------------- shipped data
def test_shipped_lists_are_consistent_in_development(engine):
    assert problems(engine) == []


def test_production_refuses_unapproved_aliases_missing_sources_and_the_paneer_proxy(engine):
    found = "\n".join(problems(engine, "production"))
    assert "no source" in found
    assert "USDA-2647442 is blocked in production" in found
    assert "food names have no approved entry" in found


def test_readiness_lists_every_unsigned_section():
    items = "\n".join(i["problem"] for i in check()["items"])
    for text in ("recipe_sources", "cooking_methods", "units", "blocked_records", "allergen_derivatives", "bad_mappings",
                 "unsafe_ingredients", "ingredient_aliases"):
        assert text in items


def test_files_are_in_the_manifest_and_unsigned():
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["swastham_data"]["white_lists"] == "white_lists.json"
    assert manifest["swastham_data"]["black_lists"] == "black_lists.json"
    for name in ("white_lists.json", "black_lists.json"):
        assert json.loads((DATA / name).read_text(encoding="utf-8"))["status"] == "PROPOSED"


# ---------------------------------------------------------------- white list: recipe sources, methods, units
def test_unknown_or_wrong_environment_recipe_source_is_a_problem(engine):
    def unknown(d): d.recipes["recipes"][0]["source"] = "a friend"
    def dev_only(d): d.recipes["recipes"][0]["source"] = "indb_recipes"
    assert any("not on the white list" in p for p in problems(engine, mutate=unknown))
    assert not any("not allowed" in p for p in problems(engine, mutate=dev_only))
    assert any("not allowed in production" in p for p in problems(engine, "production", dev_only))


def test_recipe_method_must_be_listed(engine):
    def fried(d): d.recipes["recipes"][0]["method"] = "deep fry"
    def boiled(d): d.recipes["recipes"][0]["method"] = "Boiled"
    assert any("cooking method 'deep fry'" in p for p in problems(engine, mutate=fried))
    assert not any("cooking method" in p for p in problems(engine, mutate=boiled))


@pytest.mark.parametrize("method,ok", [("boil", True), ("BOILED", True), ("  steam ", True), ("deep fry", False), ("deep-fry", False),
                                       ("", False), ("fry", False), ("microwave", False)])
def test_method_allowed(engine, method, ok):
    assert lists.method_allowed(engine.data.white_lists, method, "production") is ok


def test_method_restricted_to_an_environment():
    white = {"cooking_methods": {"entries": [{"id": "grill", "allowed_in": ["development"]}]}}
    assert lists.method_allowed(white, "grill", "development") and not lists.method_allowed(white, "grill", "production")


def test_ai_draft_with_a_method_off_the_list_is_rejected(engine):
    allowed = {i["food_id"] for i in GOOD_DRAFT["ingredients"]}
    assert engine.recipes._validate_draft(GOOD_DRAFT, allowed)[0] is not None
    assert engine.recipes._validate_draft({**GOOD_DRAFT, "method": "boil"}, allowed)[0] is not None
    for bad in ("deep fry", 5, ["boil"], {"x": 1}, ""):
        recipe, why = engine.recipes._validate_draft({**GOOD_DRAFT, "method": bad}, allowed)
        assert recipe is None and why, bad


@pytest.mark.parametrize("text", ["2 cloves", "about 2 bunches", "a squeeze", "1", "", "2 inch", "1 TBSP", "half", "1 medium",
                                  "1/2 cup", "2 tsp (to taste)"])
def test_listed_units_pass(engine, text):
    assert lists.unit_text_problem(text, lists.unit_words(engine.data.white_lists, "development")) is None


@pytest.mark.parametrize("text", ["3 handfuls", "1 glug", "2 ladles", "1 plate"])
def test_unlisted_units_are_flagged(engine, text):
    assert lists.unit_text_problem(text, lists.unit_words(engine.data.white_lists, "production")) is not None


def test_squeeze_and_katori_are_not_allowed_in_production(engine):
    words = lists.unit_words(engine.data.white_lists, "production")
    assert "squeeze" not in words and "katori" not in words and "teaspoon" in words
    assert lists.unit_text_problem("1 katori", words) is not None


def test_food_master_unit_weight_with_an_unlisted_unit_is_a_problem(engine):
    def odd(d): d.food_master["foods"][0]["unit_weights_g"] = {"handful": 30}
    # the engine's own index is what is checked, so give it the odd food directly
    original = engine.index.foods["FOOD-CHAPATI"]["unit_weights_g"]
    engine.index.foods["FOOD-CHAPATI"]["unit_weights_g"] = {"handful": 30}
    try:
        assert any("'handful'" in p for p in problems(engine))
    finally:
        engine.index.foods["FOOD-CHAPATI"]["unit_weights_g"] = original


def test_production_alias_check_accepts_only_fully_approved_rows(engine):
    def approve_all(by="Dr A", on="2026-10-01"):
        def mutate(d):
            d.white_lists["ingredient_aliases"]["entries"] = [
                {"food_id": fid, "alias": a, "approved_by": by, "approved_on": on}
                for fid, f in engine.index.foods.items() for a in [f["name"], *f.get("aliases", [])]]
        return mutate
    assert not any("no approved entry" in p for p in problems(engine, "production", approve_all()))
    for by, on in ((None, "2026-10-01"), ("TBD", "2026-10-01"), ("Dr A", None), ("Dr A", "next week"), ("Dr A", "2999-01-01")):
        assert any("no approved entry" in p for p in problems(engine, "production", approve_all(by, on))), (by, on)


@pytest.mark.parametrize("broken", [None, [], "x", {"entries": None}, {"entries": "x"}, {"entries": [1, None, "a"]}, {}])
def test_damaged_sections_never_crash(engine, broken):
    def damage(d):
        for section in ("recipe_sources", "cooking_methods", "units", "ingredient_aliases"):
            d.white_lists[section] = broken
        for section in ("blocked_records", "unsafe_ingredients", "allergen_derivatives", "bad_mappings"):
            d.black_lists[section] = broken
    assert isinstance(problems(engine, mutate=damage), list)
    assert isinstance(lists.unsigned_counts({"white_lists": {"units": broken}, "black_lists": {"bad_mappings": broken}}), list)


def test_duplicate_or_misplaced_white_list_entries_are_reported(engine):
    def dup(d):
        d.white_lists["units"]["entries"].append(copy.deepcopy(d.white_lists["units"]["entries"][0]))
        d.white_lists["cooking_methods"]["entries"][0]["allowed_in"] = ["staging"]
    found = "\n".join(problems(engine, mutate=dup))
    assert "repeated" in found and "allowed_in" in found


# ---------------------------------------------------------------- black list: blocked records
def fake_library():
    lib = Library(environment="production")
    lib.records = {"A": {"food_name": "x"}, "B": {"food_name": "y"}}
    lib.flags = {"A": {"serving_unclear"}}
    return lib


def test_blocked_records_are_removed_only_in_their_environment():
    black = {"blocked_records": {"entries": [{"record_id": "A", "blocked_in": ["production"], "reason": "r"}]}}
    lib = fake_library()
    assert lists.apply_blocked_records(lib, black, "development") == []
    assert set(lib.records) == {"A", "B"}
    assert lists.apply_blocked_records(lib, black, "production") == ["A"]
    assert set(lib.records) == {"B"} and "A" not in lib.flags
    assert lists.apply_blocked_records(lib, black, "production") == []        # twice is harmless


@pytest.mark.parametrize("entry", [{"record_id": None, "blocked_in": ["production"]}, {"record_id": "A"},
                                   {"record_id": "A", "blocked_in": "production"}, {"record_id": 5, "blocked_in": ["production"]}, "A", None])
def test_malformed_blocked_entries_remove_nothing(entry):
    lib = fake_library()
    lists.apply_blocked_records(lib, {"blocked_records": {"entries": [entry]}}, "production")
    assert set(lib.records) == {"A", "B"}


def test_a_food_whose_record_is_blocked_is_reported(engine):
    def block(d): d.black_lists["blocked_records"]["entries"].append(
        {"id": "X", "record_id": engine.index.foods["FOOD-ONION"]["ingredient_record"], "blocked_in": ["development"], "reason": "t"})
    assert any("FOOD-ONION" in p and "blocked in development" in p for p in problems(engine, mutate=block))


# ---------------------------------------------------------------- black list: unsafe ingredients
def test_condition_blocked_foods():
    black = {"unsafe_ingredients": {"entries": [{"condition": "Hypertension", "food_id": "FOOD-X"},
                                                {"condition": "diabetes", "food_id": "FOOD-Y"}, {"condition": "ckd"}, "junk"]}}
    assert lists.condition_blocked_foods(black, ["hypertension"]) == {"FOOD-X"}
    assert lists.condition_blocked_foods(black, [" HYPERTENSION ", "diabetes"]) == {"FOOD-X", "FOOD-Y"}
    assert lists.condition_blocked_foods(black, []) == set() == lists.condition_blocked_foods(black, None)
    assert lists.condition_blocked_foods({}, ["diabetes"]) == set()


def test_unsafe_food_is_blocked_for_the_person_with_the_condition(engine):
    black = {"unsafe_ingredients": {"entries": [{"condition": "diabetes", "food_id": "FOOD-SWEETS"}]}}
    filt = AllergyFilter(engine.data.allergen_map, engine.index, black)
    assert "FOOD-SWEETS" in filt.blocked_foods(Person(allergies=[], conditions=["diabetes"]))
    assert "FOOD-SWEETS" not in filt.blocked_foods(Person(allergies=[], conditions=["obesity"]))
    plain = AllergyFilter(engine.data.allergen_map, engine.index)
    assert "FOOD-SWEETS" not in plain.blocked_foods(Person(allergies=[], conditions=["diabetes"]))


def test_unsafe_rows_in_production_need_source_reviewer_and_date(engine):
    def row(**kw):
        return lambda d: d.black_lists["unsafe_ingredients"]["entries"].append(
            {"condition": "diabetes", "food_id": "FOOD-SWEETS", "source": "Dr A guidance 2026", "reviewer": "Dr A",
             "review_date": "2026-10-01", **kw})
    assert not any("unsafe_ingredients" in p for p in problems(engine, "production", row()))
    for bad in ({"source": "PLACEHOLDER"}, {"reviewer": None}, {"review_date": "soon"}, {"review_date": "2999-01-01"}, {"food_id": "FOOD-NOPE"}):
        assert any("unsafe_ingredients" in p for p in problems(engine, "production", row(**bad))), bad


# ---------------------------------------------------------------- black list: allergen derivatives
def test_derivative_words_read_as_the_allergy(engine):
    filt = AllergyFilter(engine.data.allergen_map, engine.index, engine.data.black_lists)
    assert filt.read_reply("I am allergic to whey") == ["milk"]
    assert filt.read_reply("tahini makes me sick") == ["sesame"]
    assert filt.read_reply("soy sauce") == ["soy"]
    assert filt.read_reply("no allergies") == []
    assert AllergyFilter(engine.data.allergen_map, engine.index).read_reply("I am allergic to whey") is None


def test_derivative_foods_are_blocked_with_the_allergen(engine):
    filt = AllergyFilter(engine.data.allergen_map, engine.index, engine.data.black_lists)
    assert {"FOOD-SUJI", "FOOD-CHAPATI"} <= filt.foods_for(["gluten"])
    assert {"FOOD-TOFU", "FOOD-SOYBEAN"} <= filt.foods_for(["soy"])
    assert filt.foods_for([]) == set()


def test_derivative_word_must_be_a_whole_word(engine):
    filt = AllergyFilter(engine.data.allergen_map, engine.index, engine.data.black_lists)
    assert filt.read_reply("whelk and whetstone") is None


def test_derivative_entry_problems_are_reported(engine):
    def bad(d):
        d.black_lists["allergen_derivatives"]["entries"] += [
            {"id": "T1", "allergen": "dust", "words": ["x"], "foods": []},
            {"id": "T2", "allergen": "milk", "words": [], "foods": []},
            {"id": "T3", "allergen": "milk", "words": ["x"], "foods": ["FOOD-NOPE"]}]
    found = "\n".join(problems(engine, mutate=bad))
    assert "T1" in found and "T2" in found and "T3" in found


# ---------------------------------------------------------------- black list: bad mappings
def rec(name):
    return {"food_name": name, "record_type": "prepared_food", "data_quality": {"energy_basis": "source_published"}}


def test_bad_mapping_drops_only_the_named_pair():
    lib = Library(environment="development", records={"A": rec("Groundcherries, raw"), "B": rec("Poha, flattened rice"),
                                                       "C": rec("Groundcherries jam")})
    assert LibrarySearch(lib, [("poha", "groundcherries")]).resolve("poha")["record_id"] == "B"
    assert LibrarySearch(lib, [("poha", "groundcherries")]).search("groundcherries")[0]["record_id"] in {"A", "C"}
    only_bad = LibrarySearch(Library(environment="development", records={"A": rec("Poha groundcherries")}), [("poha", "groundcherries")])
    assert only_bad.resolve("poha")["status"] == "not_found"


@pytest.mark.parametrize("pairs", [[], None, [("", "")], [("poha", "")], [("x", "y")]])
def test_odd_bad_mapping_input_changes_nothing(pairs):
    lib = Library(environment="development", records={"A": rec("Poha")})
    assert LibrarySearch(lib, pairs).resolve("poha")["record_id"] == "A"


def test_bad_mapping_pairs_ignore_broken_entries():
    black = {"bad_mappings": {"entries": [{"query_word": "poha", "blocked_name_word": "Groundcherries"}, {"query_word": ""}, {}, "x", None]}}
    assert lists.bad_mapping_pairs(black) == [("poha", "groundcherries")]


def test_real_library_poha_never_resolves_to_a_groundcherry(engine):
    search = LibrarySearch(engine.library, lists.bad_mapping_pairs(engine.data.black_lists))
    hit = search.resolve("poha")
    if hit["status"] == "found":
        assert "groundcherr" not in hit["food_name"].lower()
    assert all("groundcherr" not in h["food_name"].lower() for h in search.search("poha", 20))


def test_string_instead_of_list_never_matches_by_substring_and_is_reported(engine):
    """'blocked_in': 'production' (a string) must not block by accident, and must be reported at start-up."""
    black = {"blocked_records": {"entries": [{"record_id": "A", "blocked_in": "production"}]}}
    assert lists.blocked_record_ids(black, "production") == {}
    assert lists.blocked_record_ids({"blocked_records": {"entries": [{"record_id": "A", "blocked_in": "production"}]}}, "prod") == {}

    def bad(d):
        d.black_lists["blocked_records"]["entries"].append({"id": "S", "record_id": "A", "blocked_in": "production"})
        d.white_lists["units"]["entries"][0]["allowed_in"] = "production"
    found = "\n".join(problems(engine, mutate=bad))
    assert "blocked_records/S" in found and "units/teaspoon" in found
    assert lists.method_allowed({"cooking_methods": {"entries": [{"id": "x", "allowed_in": "production"}]}}, "x", "production") is False


def test_empty_unsafe_list_needs_a_signed_empty_review():
    base = {"white_lists": {}, "black_lists": {"unsafe_ingredients": {"entries": []}}}
    assert any("not signed as empty" in p for p in lists.unsigned_counts(base))
    for by, on in ((None, "2026-10-01"), ("TBD", "2026-10-01"), ("Dr A", None)):
        base["black_lists"]["unsafe_ingredients"].update(reviewed_empty_by=by, reviewed_empty_on=on)
        assert any("not signed as empty" in p for p in lists.unsigned_counts(base)), (by, on)
    base["black_lists"]["unsafe_ingredients"].update(reviewed_empty_by="Dr A", reviewed_empty_on="2026-10-01")
    assert not any("unsafe_ingredients" in p for p in lists.unsigned_counts(base))
