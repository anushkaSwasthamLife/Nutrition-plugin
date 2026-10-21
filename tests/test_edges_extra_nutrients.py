"""Edge cases for the six extra nutrients (plan item E): boundaries, missing data, odd input, tampered library, fuzz."""
import copy
import json
import math
import random
import shutil
from decimal import Decimal

import pytest

from dixit_answer.config import DATA_DIR, DEFAULT_LIBRARY_DIR
from dixit_answer.loader import LoadError, load_library, load_manifest
from dixit_answer.person import Person, sanitize_profile
from dixit_answer.recipes import EXTRA_NUTRIENTS, MACROS, NUTRIENTS, CheckedRecipe

D = Decimal


def recipe(items, steps=None):
    return CheckedRecipe(recipe_id="T", origin="ai_suggested", name="Test", makes="1 dish", kind="sabji",
                         ingredients=[{"food": f, "grams": g, "unit": ""} for f, g in items], steps=steps or ["Heat the oil.", "Cook with the salt."])


def per100(engine, food, key):
    rid = engine.index.foods[food]["ingredient_record"]
    return engine.library.records[rid]["nutrients_per_100g"][key]


def limits_with(engine, rows, margin="0.9"):
    cfg = copy.deepcopy(engine.recipes.limits_cfg)
    cfg["limits"] = rows
    cfg["release_margin"] = margin
    return cfg


def with_limits(engine, cfg):
    class Swap:
        def __enter__(self_):
            self_.old, engine.recipes.limits_cfg = engine.recipes.limits_cfg, cfg

        def __exit__(self_, *a):
            engine.recipes.limits_cfg = self_.old
    return Swap()


def row(condition, nutrient, value):
    return {"condition": condition, "nutrient": nutrient, "daily_max": value, "source": "TEST", "reviewer": None, "review_date": None}


# ---------------------------------------------------------------- exactness and boundaries
def test_the_release_boundary_is_exact_to_the_last_decimal(engine):
    svc = engine.recipes
    salt_na = D(str(per100(engine, "FOOD-SALT", "sodium_mg")))
    total = salt_na * D(10) / 100                                      # 10 g of salt
    for margin, limit, expect_pass in (("1", total, True), ("1", total - D("0.0001"), False), ("0.9", total / D("0.9"), True),
                                       ("0.9", total / D("0.9") - D("0.001"), False)):
        with with_limits(engine, limits_with(engine, [row("hypertension", "sodium_mg", str(limit))], margin)):
            person = Person(conditions=["hypertension"])
            r = recipe([("FOOD-SALT", 10)])
            svc.calculate(r)
            gate = svc.gate(r, person, svc.compile_limits(person), set())
            assert ("sodium_mg" not in gate.failed_nutrients) == expect_pass, (margin, limit)


def test_decimal_sums_have_no_float_noise(engine):
    svc = engine.recipes
    r = recipe([("FOOD-LAUKI", 0.1), ("FOOD-LAUKI", 0.2)])          # 0.1 + 0.2 must not become 0.30000000000000004
    r.ingredients = r.ingredients[:1] + [{"food": "FOOD-TOMATO", "grams": 0.2, "unit": ""}]
    svc.calculate(r)
    na = D(str(per100(engine, "FOOD-LAUKI", "sodium_mg"))) * D("0.1") / 100 + D(str(per100(engine, "FOOD-TOMATO", "sodium_mg"))) * D("0.2") / 100
    assert r.totals["sodium_mg"] == na and isinstance(r.totals["sodium_mg"], D)


@pytest.mark.parametrize("grams", [0, 0.0, 0.5, 1000, 999.99])
def test_extreme_gram_amounts_never_break_the_sums(engine, grams):
    r = recipe([("FOOD-SALT", grams)])
    engine.recipes.calculate(r)
    assert r.totals["sodium_mg"] == D(str(per100(engine, "FOOD-SALT", "sodium_mg"))) * D(str(grams)) / 100
    assert all(v >= 0 for v in r.totals.values())


def test_an_empty_recipe_and_an_unknown_food(engine):
    svc = engine.recipes
    r = recipe([])
    svc.calculate(r)
    assert all(v == 0 for v in r.totals.values()) and r.missing == {}
    r = recipe([("FOOD-DOES-NOT-EXIST", 50)])
    svc.calculate(r)
    assert set(r.missing) == set(NUTRIENTS) and r.totals == {}


def test_a_food_with_no_ingredient_record_is_missing_for_every_nutrient(engine):
    r = recipe([("FOOD-CHAPATI", 100)])                                 # chapati has facts but no ingredient record
    engine.recipes.calculate(r)
    assert set(r.missing) == set(NUTRIENTS)


def test_adding_an_ingredient_never_lowers_any_total(engine):
    base = recipe([("FOOD-LAUKI", 200), ("FOOD-OIL", 10)])
    more = recipe([("FOOD-LAUKI", 200), ("FOOD-OIL", 10), ("FOOD-SALT", 2)])
    engine.recipes.calculate(base)
    engine.recipes.calculate(more)
    for n in base.totals:
        if n in more.totals:
            assert more.totals[n] >= base.totals[n]


# ---------------------------------------------------------------- unknown is not zero; zero is not unknown
def test_a_published_zero_passes_and_a_missing_value_fails_the_same_limit(engine):
    svc = engine.recipes
    rid = engine.index.foods["FOOD-LAUKI"]["ingredient_record"]
    saved = engine.library.records[rid]["nutrients_per_100g"]["sugars_g"]
    cfg = limits_with(engine, [row("diabetes", "sugars_g", 25)])
    person = Person(conditions=["diabetes"])
    try:
        with with_limits(engine, cfg):
            for value, should_pass in ((0.0, True), (None, False), (0, True)):
                engine.library.records[rid]["nutrients_per_100g"]["sugars_g"] = value
                r = recipe([("FOOD-LAUKI", 250)])
                svc.calculate(r)
                gate = svc.gate(r, person, svc.compile_limits(person), set())
                assert ("sugars_g" in r.totals) == (value is not None)
                assert (not [x for x in gate.reasons if "no nutrition data" in x]) == should_pass
    finally:
        engine.library.records[rid]["nutrients_per_100g"]["sugars_g"] = saved


def test_one_unknown_ingredient_makes_the_whole_dish_unknown_for_that_nutrient(engine):
    r = recipe([("FOOD-LAUKI", 100), ("FOOD-TOMATO", 100), ("FOOD-PALAK", 100)])     # palak has no published sugars
    engine.recipes.calculate(r)
    assert "sugars_g" in r.missing and "FOOD-PALAK" in r.missing["sugars_g"] and "sugars_g" not in r.totals
    assert "sodium_mg" in r.totals


def test_a_record_that_lacks_the_key_entirely_is_the_same_as_null(engine):
    rid = engine.index.foods["FOOD-LAUKI"]["ingredient_record"]
    saved = engine.library.records[rid]["nutrients_per_100g"].pop("potassium_mg")
    try:
        r = recipe([("FOOD-LAUKI", 100)])
        engine.recipes.calculate(r)
        assert "potassium_mg" in r.missing
    finally:
        engine.library.records[rid]["nutrients_per_100g"]["potassium_mg"] = saved


@pytest.mark.parametrize("bad", ["12", "", "n/a", [], {}, True, False, float("nan"), float("inf"), float("-inf"), -1, -0.001])
def test_an_unusable_library_value_is_treated_as_no_data_and_never_crashes(engine, bad):
    """Library values are hash-checked, but if a bad one ever got in, the gate must fail safe: no data, never a wrong number."""
    rid = engine.index.foods["FOOD-LAUKI"]["ingredient_record"]
    saved = engine.library.records[rid]["nutrients_per_100g"]["sodium_mg"]
    engine.library.records[rid]["nutrients_per_100g"]["sodium_mg"] = bad
    try:
        r = recipe([("FOOD-LAUKI", 100)])
        engine.recipes.calculate(r)
        assert "sodium_mg" in r.missing and "sodium_mg" not in r.totals
        person = Person(conditions=["hypertension"])
        gate = engine.recipes.gate(r, person, engine.recipes.compile_limits(person), set())
        assert not gate.passed and "no nutrition data for lauki" in " ".join(gate.reasons)
    finally:
        engine.library.records[rid]["nutrients_per_100g"]["sodium_mg"] = saved


# ---------------------------------------------------------------- limits from several places
def test_strictest_sodium_limit_wins_across_conditions_and_clinician_targets(engine):
    svc = engine.recipes
    cfg = limits_with(engine, [row("general", "sodium_mg", 2300), row("hypertension", "sodium_mg", 1500), row("kidney_disease", "sodium_mg", 1000)])
    with with_limits(engine, cfg):
        assert svc.compile_limits(Person(conditions=["hypertension"]))["sodium_mg"].limit_max == D(1500)
        assert svc.compile_limits(Person(conditions=["hypertension", "kidney_disease"]))["sodium_mg"].limit_max == D(1000)
        assert svc.compile_limits(Person(conditions=["hypertension"], clinician_targets={"sodium_mg": 800}))["sodium_mg"].limit_max == D(800)
        assert svc.compile_limits(Person(conditions=["hypertension"], clinician_targets={"sodium_mg": 5000}))["sodium_mg"].limit_max == D(1500)
        assert svc.compile_limits(Person())["sodium_mg"].limit_max == D(2300)
        assert svc.compile_limits(Person(conditions=["no_such_condition"]))["sodium_mg"].limit_max == D(2300)


def test_recent_intake_reduces_the_limit_and_cannot_make_it_negative(engine):
    from datetime import datetime
    svc = engine.recipes
    now = datetime.now().isoformat(timespec="seconds")
    with with_limits(engine, limits_with(engine, [row("hypertension", "sodium_mg", 2000)])):
        some = svc.compile_limits(Person(conditions=["hypertension"], intake_today={"sodium_mg": 1500}, intake_logged_at=now))
        assert some["sodium_mg"].limit_max == D(500)
        over = svc.compile_limits(Person(conditions=["hypertension"], intake_today={"sodium_mg": 9000}, intake_logged_at=now))
        assert over["sodium_mg"].limit_max == D(0)
        person = Person(conditions=["hypertension"], intake_today={"sodium_mg": 9000}, intake_logged_at=now)
        r = recipe([("FOOD-LAUKI", 100)])
        svc.calculate(r)
        assert "sodium_mg" in svc.gate(r, person, svc.compile_limits(person), set()).failed_nutrients      # nothing is left to spend
        stale = svc.compile_limits(Person(conditions=["hypertension"], intake_today={"sodium_mg": 1500}, intake_logged_at="2020-01-01T00:00:00"))
        assert stale["sodium_mg"].limit_max == D(2000)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -5, 0, True, "1500", None, [], 1e12])
def test_clinician_targets_for_the_new_nutrients_are_cleaned(value):
    clean = sanitize_profile({"clinician_targets": {"sodium_mg": value, "potassium_mg": 3000, "iron_mg": 20}})
    targets = clean.get("clinician_targets", {})
    assert targets.get("potassium_mg") == 3000 and "iron_mg" not in targets
    if "sodium_mg" in targets:
        assert isinstance(targets["sodium_mg"], float) and math.isfinite(targets["sodium_mg"]) and targets["sodium_mg"] > 0


def test_every_new_nutrient_can_carry_a_limit_and_a_target(engine):
    svc = engine.recipes
    rows = [row("general", n, 10 ** 9) for n in EXTRA_NUTRIENTS]
    with with_limits(engine, limits_with(engine, rows)):
        limits = svc.compile_limits(Person(clinician_targets={n: 100 for n in EXTRA_NUTRIENTS}))
        assert set(limits) == set(EXTRA_NUTRIENTS) and all(limits[n].limit_max == D(100) and limits[n].verifiable for n in EXTRA_NUTRIENTS)


def test_cooking_factors_default_to_one_when_a_factor_is_absent(engine):
    svc = engine.recipes
    saved = svc.factors.pop("sodium_mg")
    try:
        r = recipe([("FOOD-SALT", 1)])
        svc.calculate(r)
        assert r.totals["sodium_mg"] == D(str(per100(engine, "FOOD-SALT", "sodium_mg"))) / 100
    finally:
        svc.factors["sodium_mg"] = saved


# ---------------------------------------------------------------- the library files
def test_every_approved_recipe_computes_and_has_whole_dish_sodium(engine):
    for raw in engine.data.recipes["recipes"]:
        r = engine.recipes._from_collection(raw["recipe_id"])
        engine.recipes.calculate(r)
        assert r.totals["sodium_mg"] >= 0 and r.totals["potassium_mg"] >= 0, raw["recipe_id"]


def _settings(engine, library_dir, environment="development"):
    s = copy.copy(engine.settings)
    s.library_dir, s.environment = library_dir, environment
    return s


def test_production_mode_loads_the_new_library_with_the_same_fields(engine):
    lib = load_library(_settings(engine, DEFAULT_LIBRARY_DIR, "production"), load_manifest())
    rec = next(iter(lib.records.values()))
    assert lib.report["library_version"] == "1.0.5" and all(k in rec["nutrients_per_100g"] for k in EXTRA_NUTRIENTS)
    assert not any(lib.is_indian(r) for r in lib.records)


@pytest.mark.parametrize("how", ["flip a byte", "append a space", "delete a line", "empty the file", "remove the file"])
def test_a_tampered_or_damaged_library_stops_start_up(engine, tmp_path, how):
    shutil.copytree(DEFAULT_LIBRARY_DIR / "01_data", tmp_path / "01_data")
    target = tmp_path / "01_data" / "nutrition_usda_reference.jsonl"
    data = target.read_bytes()
    if how == "flip a byte":
        target.write_bytes(data[:5000] + bytes([data[5000] ^ 1]) + data[5001:])
    elif how == "append a space":
        target.write_bytes(data + b" ")
    elif how == "delete a line":
        target.write_bytes(b"\n".join(data.split(b"\n")[1:]))
    elif how == "empty the file":
        target.write_bytes(b"")
    else:
        target.unlink()
    with pytest.raises(LoadError):
        load_library(_settings(engine, tmp_path), load_manifest())


def test_the_old_library_is_refused_because_the_pins_moved(engine):
    old = DEFAULT_LIBRARY_DIR.parent / "Swastham_Nutrition_Data_Library_v1_0_3"
    if not old.exists():
        pytest.skip("library v1.0.3 is not on this PC")
    with pytest.raises(LoadError, match="Hash mismatch"):
        load_library(_settings(engine, old), load_manifest())


def test_a_checkable_nutrient_with_no_value_anywhere_stops_start_up(engine):
    saved = {}
    for rid in {f["ingredient_record"] for f in engine.index.foods.values() if f.get("ingredient_record")}:
        saved[rid] = engine.library.records[rid]["nutrients_per_100g"]["phosphorus_mg"]
        engine.library.records[rid]["nutrients_per_100g"]["phosphorus_mg"] = None
    try:
        from dixit_answer.consistency import find_problems
        problems = find_problems(engine.data, engine.library, engine.index, "development")
        assert any("phosphorus_mg is marked checkable but no ingredient record" in p for p in problems)
    finally:
        for rid, v in saved.items():
            engine.library.records[rid]["nutrients_per_100g"]["phosphorus_mg"] = v


def test_the_workbook_release_and_library_sodium_agree_for_every_food_in_the_food_list(engine):
    released = {}
    import csv
    release_dir = DEFAULT_LIBRARY_DIR.parent / "releases"
    newest = sorted(p.name for p in release_dir.iterdir())[-1]
    with open(release_dir / newest / "nutrition_usda.csv", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            released[r["Food ID"]] = r["Sodium (mg)"]
    for food in engine.index.foods.values():
        rid = food.get("ingredient_record")
        if rid:
            lib_value = engine.library.records[rid]["nutrients_per_100g"]["sodium_mg"]
            csv_value = released[rid]
            assert (lib_value is None and csv_value == "") or math.isclose(lib_value, float(csv_value), abs_tol=1e-9), rid


# ---------------------------------------------------------------- answers
def test_a_recipe_answer_never_says_values_are_missing_just_because_a_hidden_nutrient_is(engine):
    answer = engine.answer("I have bhindi, what can I cook? recipe for bhindi sabji", None, {"allergies": []})
    assert "aren't available" not in answer.answer
    answer = engine.answer("I have palak and paneer, what can I cook?", None, {"allergies": []})
    assert "aren't available" not in answer.answer


def test_a_missing_displayed_macro_still_says_so(engine):
    rid = engine.index.foods["FOOD-LAUKI"]["ingredient_record"]
    saved = engine.library.records[rid]["nutrients_per_100g"]["fiber_g"]
    engine.library.records[rid]["nutrients_per_100g"]["fiber_g"] = None
    try:
        r = recipe([("FOOD-LAUKI", 100), ("FOOD-OIL", 5)])
        engine.recipes.calculate(r)
        assert "aren't available" in engine.recipes.totals_text(r)
    finally:
        engine.library.records[rid]["nutrients_per_100g"]["fiber_g"] = saved


# ---------------------------------------------------------------- fuzz
def test_seeded_fuzz_of_random_recipes_keeps_every_invariant(engine):
    rng = random.Random(20261016)
    foods = [f for f, v in engine.index.foods.items() if v.get("ingredient_record")]
    persons = [Person(), Person(conditions=["hypertension"]), Person(conditions=["diabetes", "obesity"]), Person(clinician_targets={"sodium_mg": 300})]
    svc = engine.recipes
    for _ in range(400):
        items = [(rng.choice(foods), round(rng.uniform(0.5, 600), rng.choice([0, 1, 2]))) for _ in range(rng.randint(1, 7))]
        r = recipe(items)
        svc.calculate(r)
        assert set(r.totals) | set(r.missing) == set(NUTRIENTS) and not set(r.totals) & set(r.missing)
        for n, total in r.totals.items():
            expected = sum(D(str(per100(engine, f, n))) * D(str(g)) / 100 for f, g in items)
            assert total == expected and total >= 0
        person = rng.choice(persons)
        gate = svc.gate(r, person, svc.compile_limits(person), set())
        assert isinstance(gate.passed, bool) and (gate.passed == (not gate.reasons))
        if gate.passed:
            for n, limit in svc.compile_limits(person).items():
                if limit.verifiable:
                    assert n in r.totals and r.totals[n] <= limit.release_at
