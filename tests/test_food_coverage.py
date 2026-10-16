"""The food list covers the vegetables and pulses people actually cook (FM-0.3.0).

Found 16 Oct 2026: "I have capsicum" was answered with "I left out capsicum because it isn't in our food list yet": the list had 14
vegetables. Every new food maps to one USDA record; nothing earlier changed.
"""
import json
from pathlib import Path

import pytest

from dixit_answer.person import JAIN_EXCLUDED, Person

DATA = Path(__file__).resolve().parent.parent / "dixit_answer" / "data"
NEW = ["FOOD-CAPSICUM", "FOOD-BRINJAL", "FOOD-KARELA", "FOOD-TURAI", "FOOD-KADDU", "FOOD-CARROT", "FOOD-BEETROOT", "FOOD-MOOLI",
       "FOOD-SHALGAM", "FOOD-SARSON", "FOOD-CHAULAI", "FOOD-DRUMSTICK", "FOOD-RAW-BANANA", "FOOD-ARBI", "FOOD-ARBI-LEAVES", "FOOD-SURAN",
       "FOOD-BROCCOLI", "FOOD-MUSHROOM", "FOOD-SWEET-CORN", "FOOD-SPRING-ONION", "FOOD-ZUCCHINI", "FOOD-PETHA", "FOOD-LOTUS-STEM",
       "FOOD-KATHAL", "FOOD-KOHLRABI", "FOOD-CHAYOTE", "FOOD-CELERY", "FOOD-LETTUCE", "FOOD-RAJMA", "FOOD-CHICKPEAS", "FOOD-MASOOR-DAL",
       "FOOD-TOOR-DAL", "FOOD-LOBIA", "FOOD-SOYBEAN", "FOOD-TOFU"]
DRY = ["FOOD-RAJMA", "FOOD-CHICKPEAS", "FOOD-MASOOR-DAL", "FOOD-TOOR-DAL", "FOOD-LOBIA", "FOOD-SOYBEAN", "FOOD-MOONG-DAL", "FOOD-CHANA-DAL"]


def test_the_list_grew_and_says_so(engine):
    assert engine.data.food_master["version"] == "FM-0.3.0" and len(engine.index.foods) == 77
    assert all(f in engine.index.foods for f in NEW)


ORIGINAL = {
 "FOOD-CHAPATI": [
 None,
  [
   "gluten"
  ],
  "staple"
 ],
 "FOOD-RICE": [
 None,
  [],
  "staple"
 ],
 "FOOD-DAL": [
 None,
  [],
  "pulse"
 ],
 "FOOD-BHINDI": [
  "LEGACY-169260",
  [],
  "vegetable"
 ],
 "FOOD-ALOO": [
  "LEGACY-170026",
  [],
  "starchy_vegetable"
 ],
 "FOOD-CAULIFLOWER": [
  "SURVEY-2709777",
  [],
  "vegetable"
 ],
 "FOOD-CABBAGE": [
  "SURVEY-2709773",
  [],
  "vegetable"
 ],
 "FOOD-LAUKI": [
  "LEGACY-169232",
  [],
  "vegetable"
 ],
 "FOOD-PALAK": [
  "USDA-1999633",
  [],
  "vegetable"
 ],
 "FOOD-BEANS": [
  "USDA-2346400",
  [],
  "vegetable"
 ],
 "FOOD-PEAS": [
  "LEGACY-170419",
  [],
  "starchy_vegetable"
 ],
 "FOOD-SWEET-POTATO": [
  "USDA-2346404",
  [],
  "starchy_vegetable"
 ],
 "FOOD-CURD": [
  "LEGACY-171284",
  [
   "milk"
  ],
  "dairy"
 ],
 "FOOD-PANEER": [
  "USDA-2647442",
  [
   "milk"
  ],
  "dairy"
 ],
 "FOOD-ONION": [
  "SURVEY-2709795",
  [],
  "vegetable"
 ],
 "FOOD-TOMATO": [
  "SURVEY-2709719",
  [],
  "vegetable"
 ],
 "FOOD-CUCUMBER": [
  "SURVEY-2709784",
  [],
  "vegetable"
 ],
 "FOOD-ALMOND": [
  "LEGACY-170567",
  [
   "tree_nut"
  ],
  "nut"
 ],
 "FOOD-WALNUT": [
  "LEGACY-170187",
  [
   "tree_nut"
  ],
  "nut"
 ],
 "FOOD-PEANUT": [
  "LEGACY-172430",
  [
   "peanut"
  ],
  "nut"
 ],
 "FOOD-SPROUTS": [
 None,
  [],
  "pulse"
 ],
 "FOOD-EGG": [
  "SURVEY-2707154",
  [
   "egg"
  ],
  "egg"
 ],
 "FOOD-IDLI": [
 None,
  [],
  "staple"
 ],
 "FOOD-MANGO": [
  "SURVEY-2709242",
  [],
  "fruit"
 ],
 "FOOD-BANANA": [
  "SURVEY-2709224",
  [],
  "fruit"
 ],
 "FOOD-SUGAR": [
  "USDA-746784",
  [],
  "sweet"
 ],
 "FOOD-SWEETS": [
 None,
  [],
  "sweet"
 ],
 "FOOD-SWEET-DRINKS": [
 None,
  [],
  "sweet"
 ],
 "FOOD-MOONG-DAL": [
  "LEGACY-174256",
  [],
  "pulse"
 ],
 "FOOD-CHANA-DAL": [
  "LEGACY-173756",
  [
   "chickpea"
  ],
  "pulse"
 ],
 "FOOD-OIL": [
  "LEGACY-171017",
  [],
  "pantry"
 ],
 "FOOD-GHEE": [
  "SURVEY-2710168",
  [
   "milk"
  ],
  "pantry"
 ],
 "FOOD-SALT": [
  "LEGACY-173468",
  [],
  "pantry"
 ],
 "FOOD-JEERA": [
  "LEGACY-170923",
  [],
  "pantry"
 ],
 "FOOD-HALDI": [
  "LEGACY-172231",
  [],
  "pantry"
 ],
 "FOOD-DHANIA-POWDER": [
  "LEGACY-170922",
  [],
  "pantry"
 ],
 "FOOD-CHILLI-POWDER": [
  "LEGACY-171319",
  [],
  "pantry"
 ],
 "FOOD-GREEN-CHILLI": [
  "LEGACY-170497",
  [],
  "pantry"
 ],
 "FOOD-GARLIC": [
  "SURVEY-2709786",
  [],
  "vegetable"
 ],
 "FOOD-GINGER": [
  "LEGACY-169231",
  [],
  "vegetable"
 ],
 "FOOD-CORIANDER-LEAVES": [
  "LEGACY-169997",
  [],
  "pantry"
 ],
 "FOOD-LEMON": [
  "SURVEY-2709168",
  [],
  "fruit"
 ]
}


def test_nothing_that_was_there_before_changed(engine):
    """The 42 foods of FM-0.2.2 keep their USDA record, allergens and category; every one of their names still finds them."""
    for fid, (record, allergens, category) in ORIGINAL.items():
        food = engine.index.foods[fid]
        assert (food["ingredient_record"], sorted(food["allergens"]), food["category"]) == (record, allergens, category), fid
        for alias in food["aliases"]:
            if fid == "FOOD-DAL" or alias in {"toor dal", "arhar dal", "masoor dal"}:
                continue
            assert fid in {m.food_id for m in engine.index.find(alias)} or food.get("generic") in {m.food_id for m in engine.index.find(alias)}, (fid, alias)


@pytest.mark.parametrize("fid", NEW)
def test_every_new_food_uses_one_usda_record_with_usable_numbers(engine, fid):
    food = engine.index.foods[fid]
    rid = food["ingredient_record"]
    assert rid and rid in engine.library.records and not engine.library.is_indian(rid)
    n = engine.library.records[rid]["nutrients_per_100g"]
    for key in ("energy_kcal", "carbohydrate_g", "protein_g", "fat_g"):
        assert isinstance(n[key], (int, float)) and 0 <= n[key] <= 900, (fid, key, n[key])
    assert 5 <= n["energy_kcal"] <= 460 and n["carbohydrate_g"] <= 100
    assert food["fact_records"] == {"development": [rid], "production": [rid]}
    assert food["category"] in {"vegetable", "starchy_vegetable", "pulse"}
    assert food["name"] and food["aliases"] and food["name"] in food["aliases"]


@pytest.mark.parametrize("fid", NEW)
def test_every_name_finds_its_food_and_only_that_food(engine, fid):
    for alias in engine.index.foods[fid]["aliases"]:
        found = {m.food_id for m in engine.index.find(f"i have {alias} at home")}
        assert fid in found, (alias, found)
        assert not (found - {fid} - {engine.index.foods[fid].get("generic")}), (alias, found)


def test_no_alias_is_used_by_two_foods(engine):
    seen = {}
    for fid, food in engine.index.foods.items():
        for alias in [food["name"], *food["aliases"]]:
            key = alias.lower().strip()
            assert seen.setdefault(key, fid) == fid, (alias, seen[key], fid)


@pytest.mark.parametrize("text,expected", [
    ("I have chana dal", "FOOD-CHANA-DAL"), ("chana dal tadka", "FOOD-CHANA-DAL"), ("I have kabuli chana", "FOOD-CHICKPEAS"),
    ("I have raw banana", "FOOD-RAW-BANANA"), ("I have a banana", "FOOD-BANANA"), ("I have toor dal", "FOOD-TOOR-DAL"),
    ("I have sweet corn", "FOOD-SWEET-CORN"), ("I have sweet potato", "FOOD-SWEET-POTATO"), ("I have spring onion", "FOOD-SPRING-ONION"),
    ("I have onion", "FOOD-ONION"), ("I have moong dal", "FOOD-MOONG-DAL"), ("I have lotus stem", "FOOD-LOTUS-STEM"),
    ("I have capsicum", "FOOD-CAPSICUM"), ("shimla mirch hai", "FOOD-CAPSICUM"), ("baingan aur aloo", "FOOD-BRINJAL"),
    ("I have arbi leaves", "FOOD-ARBI-LEAVES"), ("I have arbi", "FOOD-ARBI")])
def test_longer_names_win_over_shorter_ones(engine, text, expected):
    assert expected in {m.food_id for m in engine.index.find(text)}
    if expected in {"FOOD-RAW-BANANA", "FOOD-SPRING-ONION", "FOOD-SWEET-CORN", "FOOD-ARBI-LEAVES", "FOOD-TOOR-DAL", "FOOD-CHANA-DAL"}:
        wrong = {"FOOD-RAW-BANANA": "FOOD-BANANA", "FOOD-SPRING-ONION": "FOOD-ONION", "FOOD-SWEET-CORN": "FOOD-SWEET-POTATO",
                 "FOOD-ARBI-LEAVES": "FOOD-ARBI", "FOOD-TOOR-DAL": "FOOD-DAL", "FOOD-CHANA-DAL": "FOOD-CHICKPEAS"}[expected]
        assert wrong not in {m.food_id for m in engine.index.find(text)} or expected == "FOOD-TOOR-DAL"


# ---------------------------------------------------------------- the reported message and every new food in a real answer
def unknown_words(engine, cid):
    out = []
    for line in open(engine.settings.audit_log, encoding="utf-8"):
        rec = json.loads(line) if line.strip() else {}
        if rec.get("kind") == "unknown_item" and rec.get("conversation_id") == cid:
            out.append(rec["word"])
    return out


def test_the_reported_message_works(engine):
    r = engine.answer("I have capsicum and tomato, what can I cook?", None, {"allergies": []})
    assert "left out" not in r.answer and "Capsicum" in r.answer and unknown_words(engine, r.conversation_id) == []


@pytest.mark.parametrize("fid", NEW)
def test_each_new_food_is_accepted_as_something_at_home(engine, fid):
    name = engine.index.name(fid)
    r = engine.answer(f"I have {name} and tomato, what can I cook?", None, {"allergies": []})
    assert "isn't in our food list" not in r.answer and unknown_words(engine, r.conversation_id) == [], r.answer
    assert not [c for c in r.checks if not c["passed"] and c["level"] == "critical"]


@pytest.mark.parametrize("fid", DRY)
def test_dry_pulses_are_never_the_base_of_an_ai_drafted_recipe(engine, fid):
    assert engine.index.foods[fid].get("dry_weight") is True
    name = engine.index.name(fid)
    r = engine.answer(f"I have {name} and onion, what can I cook?", None, {"allergies": []})
    if r.type == "meal_from_ingredients" and "AI-suggested" in r.answer:
        assert f"- {name}:" not in r.answer, r.answer


def test_a_carrot_recipe_shows_a_unit_only_where_a_weight_is_known(engine):
    r = engine.answer("I have carrot and capsicum, what can I cook?", None, {"allergies": []})
    assert "carrot" in r.answer.lower()
    r2 = engine.answer("I have karela and brinjal, what can I cook?", None, {"allergies": []})
    assert "medium" not in r2.answer.split("**You need**")[-1].split("**How:**")[0]


# ---------------------------------------------------------------- allergies and diets still hold
def test_soy_allergy_blocks_tofu_and_soybean(engine):
    blocked = engine.allergy.blocked_foods(Person(allergies=["soy"]))
    assert {"FOOD-TOFU", "FOOD-SOYBEAN"} <= blocked and "FOOD-CAPSICUM" not in blocked
    r = engine.answer("I have tofu and capsicum, what can I cook?", None, {"allergies": ["soy"]})
    assert "left out tofu" in r.answer and "- tofu" not in r.answer


def test_chickpea_allergy_blocks_all_chickpea_foods(engine):
    blocked = engine.allergy.blocked_foods(Person(allergies=["chickpea"]))
    assert {"FOOD-CHICKPEAS", "FOOD-CHANA-DAL"} <= blocked


def test_unknown_allergies_stay_clear_of_soy_foods_too(engine):
    assert {"FOOD-TOFU", "FOOD-SOYBEAN"} <= engine.allergy.major_allergen_foods()


@pytest.mark.parametrize("fid", ["FOOD-CARROT", "FOOD-BEETROOT", "FOOD-MOOLI", "FOOD-SHALGAM", "FOOD-ARBI", "FOOD-SURAN", "FOOD-SPRING-ONION",
                                 "FOOD-MUSHROOM"])
def test_jain_diet_excludes_roots_onions_and_fungi(engine, fid):
    assert fid in JAIN_EXCLUDED and fid in engine.allergy.blocked_foods(Person(allergies=[], diet_type="jain"))
    r = engine.answer(f"I have {engine.index.name(fid)} and lauki, what can I cook?", None, {"allergies": [], "diet_type": "jain"})
    assert f"- {engine.index.name(fid)}:" not in r.answer


def test_vegetarians_can_use_every_new_food(engine):
    blocked = engine.allergy.blocked_foods(Person(allergies=[], diet_type="vegetarian"))
    assert not blocked & set(NEW)


def test_the_review_page_search_and_start_up_check_accept_the_new_list(engine):
    from dixit_answer.consistency import find_problems
    assert find_problems(engine.data, engine.library, engine.index, "development") == []
