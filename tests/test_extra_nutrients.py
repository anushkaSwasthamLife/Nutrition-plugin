"""Plan item E: sodium, total sugars, saturated fat, cholesterol, potassium and phosphorus (library v1.0.4, USDA workbook Fix 20)."""
import copy
import dataclasses
import json
from decimal import Decimal

import pytest

from dixit_answer.config import load_settings
from dixit_answer.consistency import find_problems
from dixit_answer.person import Person
from dixit_answer.recipes import EXTRA_NUTRIENTS, CheckedRecipe

OLD_LIBRARY = load_settings().nutrition_dir / "Swastham_Nutrition_Data_Library_v1_0_4" / "01_data" / "nutrition_usda_reference.jsonl"


def _recipe(items, steps=None):
    return CheckedRecipe(recipe_id="T", origin="ai_suggested", name="Test", makes="1 dish", kind="sabji",
                         ingredients=[{"food": f, "grams": g, "unit": ""} for f, g in items],
                         steps=steps or ["Heat the oil.", "Cook the vegetables with the salt."])


def test_library_v104_carries_the_six_nutrients(engine):
    usda = [r for r in engine.library.records.values() if r.get("source_collection") == "USDA_NUTRIENT_REFERENCE"]
    assert len(usda) == 13560 and engine.library.report["library_version"] == "1.0.5"
    for rec in usda:
        for key in EXTRA_NUTRIENTS:
            assert key in rec["nutrients_per_100g"] and key in rec["nutrients_per_serving"]
    filled = {k: sum(1 for r in usda if r["nutrients_per_100g"][k] is not None) for k in EXTRA_NUTRIENTS}
    assert filled["sodium_mg"] > 13400 and filled["sugars_g"] > 11500 and filled["potassium_mg"] > 13200
    bhindi = engine.library.records["LEGACY-169260"]["nutrients_per_100g"]
    assert bhindi["sodium_mg"] is not None and bhindi["sodium_mg"] >= 0


def test_unknown_stays_null_and_is_never_zero(engine):
    usda = [r for r in engine.library.records.values() if r.get("source_collection") == "USDA_NUTRIENT_REFERENCE"]
    unknown_sugars = [r for r in usda if r["nutrients_per_100g"]["sugars_g"] is None]
    assert len(unknown_sugars) > 1000
    assert all(r["nutrients_per_100g"]["sugars_g"] is None for r in unknown_sugars)
    assert not any("added_sugar_g" in r["nutrients_per_100g"] for r in usda)        # no USDA download has it


@pytest.mark.skipif(not OLD_LIBRARY.exists(), reason="library v1.0.4 is not on this PC")
def test_nothing_but_the_corrected_values_changed_since_v104(engine):
    """v1.0.5 corrects values and status; every identity field of every v1.0.4 record, and the six extra nutrients, stay as they were."""
    checked = 0
    for line in OLD_LIBRARY.read_text(encoding="utf-8").splitlines():
        old = json.loads(line)
        rec = engine.library.records.get(old["nutrition_record_id"])
        assert rec is not None, old["nutrition_record_id"]
        for key in ("nutrition_record_id", "source_record_id", "normalized_name", "aliases", "source_collection", "record_type", "category", "subtype",
                    "serving", "retrieval"):
            assert rec[key] == old[key], (old["nutrition_record_id"], key)
        for key in EXTRA_NUTRIENTS:
            assert rec["nutrients_per_100g"][key] == old["nutrients_per_100g"][key], (old["nutrition_record_id"], key)
        checked += 1
    assert checked == 13554
    assert len([r for r in engine.library.records.values() if r.get("source_collection") == "USDA_NUTRIENT_REFERENCE"]) == checked + 6


def test_totals_add_up_the_new_nutrients_exactly(engine):
    svc = engine.recipes
    recipe = _recipe([("FOOD-LAUKI", 250), ("FOOD-SALT", 3)])
    svc.calculate(recipe)
    salt = Decimal(str(engine.library.records[engine.index.foods["FOOD-SALT"]["ingredient_record"]]["nutrients_per_100g"]["sodium_mg"]))
    lauki = Decimal(str(engine.library.records[engine.index.foods["FOOD-LAUKI"]["ingredient_record"]]["nutrients_per_100g"]["sodium_mg"]))
    assert recipe.totals["sodium_mg"] == lauki * Decimal(250) / 100 + salt * Decimal(3) / 100
    assert "sugars_g" in recipe.missing and "FOOD-LAUKI" in recipe.missing["sugars_g"]      # USDA publishes no sugars for lauki


def test_too_much_salt_fails_the_blood_pressure_limit_and_a_half_salt_repair_fixes_it(engine):
    svc, person = engine.recipes, Person(conditions=["hypertension"])
    limits = svc.compile_limits(person)
    assert limits["sodium_mg"].verifiable
    salty = _recipe([("FOOD-LAUKI", 250), ("FOOD-SALT", 6)])
    svc.calculate(salty)
    gate = svc.gate(salty, person, limits, set())
    assert not gate.passed and "sodium" in " ".join(gate.reasons) and "sodium_mg" in gate.failed_nutrients
    assert svc.repair(salty, gate) == "less salt"
    svc.calculate(salty)
    again = svc.gate(salty, person, limits, set())
    assert "sodium_mg" not in again.failed_nutrients


def test_a_missing_sodium_value_is_missing_data_not_zero(engine):
    svc, person = engine.recipes, Person(conditions=["hypertension"])
    rid = engine.index.foods["FOOD-LAUKI"]["ingredient_record"]
    saved = engine.library.records[rid]["nutrients_per_100g"]["sodium_mg"]
    engine.library.records[rid]["nutrients_per_100g"]["sodium_mg"] = None
    try:
        recipe = _recipe([("FOOD-LAUKI", 250)])
        svc.calculate(recipe)
        gate = svc.gate(recipe, person, svc.compile_limits(person), set())
        assert "sodium_mg" not in recipe.totals and not gate.passed and "no nutrition data for lauki" in " ".join(gate.reasons)
    finally:
        engine.library.records[rid]["nutrients_per_100g"]["sodium_mg"] = saved


def test_a_total_sugar_limit_can_now_be_checked(engine):
    svc = engine.recipes
    cfg = copy.deepcopy(svc.limits_cfg)
    cfg["limits"].append({"condition": "diabetes", "nutrient": "sugars_g", "daily_max": 25, "source": "PLACEHOLDER", "reviewer": None, "review_date": None})
    old_cfg, svc.limits_cfg = svc.limits_cfg, cfg
    try:
        person = Person(conditions=["diabetes"])
        limits = svc.compile_limits(person)
        assert limits["sugars_g"].verifiable
        sweet = _recipe([("FOOD-ALOO", 200), ("FOOD-SUGAR", 40)])
        svc.calculate(sweet)
        gate = svc.gate(sweet, person, limits, set())
        assert "sugars_g" in gate.failed_nutrients and "sugar" in " ".join(gate.reasons)
        assert svc.repair(sweet, gate) is not None
    finally:
        svc.limits_cfg = old_cfg


def test_extra_nutrients_never_appear_in_answers(engine, chat):
    answers = " ".join(r.answer.lower() for r in chat("what can I cook with aloo and onion?", "I have high bp, what can I cook with lauki and tomato?"))
    for word in ("sodium", "potassium", "phosphorus", "cholesterol", "saturated"):
        assert word not in answers


def test_startup_check_catches_a_checkable_nutrient_the_library_does_not_carry(engine):
    limits = copy.deepcopy(engine.data.condition_limits)
    limits["verifiable_nutrients"].append("added_sugar_g")                     # not in the recipe maths
    problems = find_problems(dataclasses.replace(engine.data, condition_limits=limits), engine.library, engine.index, "development")
    assert any("added_sugar_g is marked checkable but the recipe maths" in p for p in problems)
    limits = copy.deepcopy(engine.data.condition_limits)
    limits["limits"].append({"condition": "general", "nutrient": "iron_mg", "daily_max": 10, "source": "x", "reviewer": None, "review_date": None})
    problems = find_problems(dataclasses.replace(engine.data, condition_limits=limits), engine.library, engine.index, "development")
    assert any("iron_mg is not a nutrient we know" in p for p in problems)
    assert find_problems(engine.data, engine.library, engine.index, "development") == []


def test_manifest_pins_the_new_library(engine):
    assert engine.data.manifest["library"]["version"] == "1.0.5"
    assert engine.versions["library"] == "1.0.5"


def test_ai_drafting_now_runs_for_blood_pressure_and_a_salty_draft_is_repaired(settings):
    from test_phase5 import draft, failed, make_engine
    salty = draft("Salty Lauki Sabji", [("FOOD-LAUKI", 250), ("FOOD-TOMATO", 100), ("FOOD-OIL", 9), ("FOOD-SALT", 7)],
                  ["Heat the oil.", "Add the lauki and tomato with salt and cook for 10 minutes till soft."])
    engine, llm = make_engine(settings, [salty])
    r = engine.answer("I have lauki and tomato, what can I cook?", None, {"allergies": [], "conditions": ["hypertension"]})
    assert len(llm.requests) == 1 and "AI-suggested" in r.answer and "less salt" in r.answer and not failed(r)
    assert "sodium_mg" in llm.requests[0]["limits_whole_dish"]
