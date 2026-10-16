"""Many dishes for what a person has (RCP-0.2.0): "I have moong dal" gets khichdi, chilla, tadka and more, not one dish or none.

Found 16 Oct 2026: "I have moong daal, what can I make" answered "We don't have a checked recipe for this yet".
"""
import json
from pathlib import Path

import pytest

from edge_helpers import assert_valid

DATA = Path(__file__).resolve().parent.parent / "dixit_answer" / "data"
PROFILE = {"allergies": ["gluten", "egg"], "conditions": ["obesity"], "diet_type": "jain"}      # the profile in the reported chat


def dishes(answer):
    return [line[2:] for line in answer.splitlines() if line.startswith("- ") and "Which one" not in line]


def ask(engine, text, profile=None, cid=None):
    r = engine.answer(text, cid, {"allergies": []} if profile is None else profile)
    assert_valid(r, text)
    return r


# ---------------------------------------------------------------- the reported message
def test_moong_daal_for_the_reported_profile_lists_several_dishes(full_engine):
    r = ask(full_engine, "i have moong daal, what can i make", PROFILE)
    names = dishes(r.answer)
    assert r.answer.startswith("With moong dal you can make any of these:")
    assert {"Moong Dal Khichdi", "Moong Dal Chilla", "Moong Dal Soup"} <= set(names), names
    assert "Which one should I show?" in r.answer and not r.fallback


def test_each_listed_dish_can_be_picked_and_is_shown_in_full(full_engine):
    first = ask(full_engine, "i have moong dal", PROFILE)
    for name in [n.split(" (also needs")[0] for n in dishes(first.answer)]:
        r = ask(full_engine, name, PROFILE, first.conversation_id)
        assert "**You need**" in r.answer and f"**{name}**" in r.answer and not r.fallback, (name, r.answer[:200])


@pytest.mark.parametrize("word", ["moong dal", "moong daal", "mung dal", "dal", "daal"])
def test_every_spelling_of_moong_dal_finds_dishes(full_engine, word):
    r = ask(full_engine, f"I have {word}, what can I make?")
    assert len(dishes(r.answer)) >= 2 or "**You need**" in r.answer, (word, r.answer[:300])
    assert "We don't have a checked recipe" not in r.answer


def test_a_plain_profile_gets_the_onion_dishes_too(full_engine):
    names = dishes(ask(full_engine, "I have moong dal", {"allergies": []}).answer)
    assert "Moong Dal Tadka" in names and "Jain Moong Dal Tadka" in names and len(names) >= 5


# ---------------------------------------------------------------- the halwa
def test_halwa_is_never_offered_unasked(full_engine):
    for profile in (PROFILE, {"allergies": []}, {"allergies": [], "conditions": ["obesity"]}):
        assert not any("halwa" in n.lower() for n in dishes(ask(full_engine, "I have moong dal", profile).answer))


@pytest.mark.parametrize("text", ["how do I make moong dal halwa?", "moong dal ka halwa recipe", "halwa moong daal ka", "give me the recipe for moong halwa"])
def test_asked_for_by_name_the_halwa_gets_an_honest_answer_and_moong_dal_alternatives(full_engine, text):
    r = ask(full_engine, text)
    assert "I can't show Moong Dal Halwa" in r.answer and "isn't a home-meal dish" in r.answer
    assert any("Moong Dal" in n for n in dishes(r.answer)), r.answer
    assert "I don't have a checked recipe" not in r.answer


def test_the_halwa_never_passes_the_gate_for_anyone(full_engine):
    from dixit_answer.person import Person
    for person in (Person(allergies=[]), Person(allergies=[], conditions=["obesity"]), Person(allergies=[], conditions=["diabetes"])):
        limits = full_engine.recipes.compile_limits(person)
        recipe = full_engine.recipes._from_collection("RCP-SWEET-001")
        full_engine.recipes.calculate(recipe)
        assert not full_engine.recipes.gate(recipe, person, limits, set()).passed


# ---------------------------------------------------------------- every food has dishes
def food_ids(engine):
    return [f for f, food in engine.index.foods.items() if food["category"] in {"vegetable", "starchy_vegetable", "pulse"}
            and f not in {"FOOD-GARLIC", "FOOD-GINGER", "FOOD-SPROUTS", "FOOD-DAL", "FOOD-SOYBEAN", "FOOD-SPRING-ONION"}]


def test_every_vegetable_and_pulse_is_the_main_food_of_at_least_one_dish(full_engine):
    used = {m for r in full_engine.recipes.recipes.values() for m in r["main_ingredients"]}
    assert [f for f in food_ids(full_engine) if f not in used] == []


@pytest.mark.parametrize("fid", ["FOOD-MOONG-DAL", "FOOD-TOOR-DAL", "FOOD-MASOOR-DAL", "FOOD-CHANA-DAL", "FOOD-RAJMA", "FOOD-CHICKPEAS", "FOOD-LOBIA",
                                 "FOOD-TOFU", "FOOD-ALOO", "FOOD-PALAK", "FOOD-CAPSICUM", "FOOD-LAUKI", "FOOD-CAULIFLOWER", "FOOD-BRINJAL"])
def test_the_main_foods_have_several_dishes(full_engine, fid):
    assert sum(1 for r in full_engine.recipes.recipes.values() if fid in r["main_ingredients"]) >= 2, fid


@pytest.mark.parametrize("fid", sorted(f for f in ["FOOD-" + x for x in "CAPSICUM BRINJAL KARELA TURAI KADDU CARROT BEETROOT MOOLI SHALGAM SARSON CHAULAI DRUMSTICK RAW-BANANA ARBI ARBI-LEAVES SURAN BROCCOLI MUSHROOM SWEET-CORN ZUCCHINI PETHA LOTUS-STEM KATHAL KOHLRABI CHAYOTE CELERY LETTUCE RAJMA CHICKPEAS MASOOR-DAL TOOR-DAL LOBIA TOFU BESAN SUJI RICE MOONG-DAL CHANA-DAL BHINDI CABBAGE LAUKI PALAK BEANS PEAS TOMATO ALOO CAULIFLOWER SWEET-POTATO".split()]))
def test_asking_with_any_food_gives_a_checked_dish_or_a_list(full_engine, fid):
    name = full_engine.index.name(fid)
    r = ask(full_engine, f"I have {name}, what can I cook?")
    assert not r.fallback, (name, r.answer[:200])
    assert "We don't have a checked recipe" not in r.answer or full_engine.index.foods[fid].get("dry_weight"), (name, r.answer[:200])
    assert dishes(r.answer) or "**You need**" in r.answer, (name, r.answer[:300])


# ---------------------------------------------------------------- the profile still decides
@pytest.mark.parametrize("allergy,banned", [("milk", ["paneer", "ghee"]), ("gluten", ["suji", "chapati"]), ("chickpea", ["besan", "chickpeas", "chana dal"]),
                                            ("soy", ["tofu"]), ("peanut", ["peanut"])])
def test_no_listed_or_shown_dish_contains_an_allergen(full_engine, allergy, banned):
    for text in ["I have moong dal and capsicum", "I have tomato", "give me recipes", "I have lauki", "I have palak"]:
        r = ask(full_engine, text, {"allergies": [allergy]})
        body = r.answer.lower().split("kept in mind")[0]
        for word in banned:
            assert f"- {word}" not in body and f"**{word}" not in body, (allergy, text, word)
        for name in [n.split(" (also needs")[0] for n in dishes(r.answer)]:
            shown = ask(full_engine, name, {"allergies": [allergy]}, r.conversation_id)
            assert not shown.fallback, (allergy, name)
            for word in banned:
                assert f"- {word}:" not in shown.answer.lower(), (allergy, name, word)


def test_a_jain_profile_never_gets_a_dish_with_onion_garlic_or_roots(full_engine):
    from dixit_answer.person import JAIN_EXCLUDED
    bad = [n.lower() for fid in JAIN_EXCLUDED for n in [full_engine.index.name(fid)]]
    for text in ["I have moong dal", "I have capsicum", "I have rajma", "I have brinjal", "I have lauki and tomato", "give me recipes"]:
        r = ask(full_engine, text, {"allergies": [], "diet_type": "jain"})
        for name in [n.split(" (also needs")[0] for n in dishes(r.answer)]:
            shown = ask(full_engine, name, {"allergies": [], "diet_type": "jain"}, r.conversation_id)
            lines = [line for line in shown.answer.lower().splitlines() if line.startswith("- ")]
            assert not [x for x in lines for b in bad if x.startswith(f"- {b}:")], (text, name)


def test_the_choices_are_marked_when_one_more_food_is_needed(full_engine):
    r = ask(full_engine, "I have capsicum")
    assert any("(also needs" in n for n in dishes(r.answer))
    picked = [n for n in dishes(r.answer) if "(also needs" in n][0]
    shown = ask(full_engine, picked.split(" (also needs")[0], None, r.conversation_id)
    assert "You'd also need" in shown.answer


def test_unknown_allergies_still_ask_first_and_still_list_dishes(full_engine):
    r = full_engine.answer("I have moong dal, what can I cook?", None, None)
    assert "food allergies" in r.answer and dishes(r.answer)


def test_a_single_matching_dish_is_shown_in_full(full_engine):
    r = ask(full_engine, "I have masoor dal and palak", PROFILE | {"diet_type": "vegetarian"})
    assert r.type == "meal_from_ingredients" and ("**You need**" in r.answer or dishes(r.answer))


def test_at_most_six_dishes_are_listed(full_engine):
    for text in ["I have dal", "I have aloo", "I have lauki", "give me recipes"]:
        assert len(dishes(ask(full_engine, text).answer)) <= 6


def test_ai_drafts_are_still_the_last_resort_for_foods_without_an_approved_dish(full_engine):
    r = ask(full_engine, "I have zorblax and tomato")
    assert "zorblax" in r.answer and "passed to our dietitian" in r.answer


# ---------------------------------------------------------------- the data
def test_the_collection_is_complete_and_every_recipe_but_the_halwa_passes_for_a_plain_profile(full_engine):
    from dixit_answer.person import Person
    person = Person(allergies=[])
    limits = full_engine.recipes.compile_limits(person)
    failing = []
    for rid in full_engine.recipes.recipes:
        recipe = full_engine.recipes._from_collection(rid)
        full_engine.recipes.calculate(recipe)
        gate = full_engine.recipes.gate(recipe, person, limits, set())
        if not gate.passed:
            failing.append(rid)
    assert failing == ["RCP-SWEET-001"] and len(full_engine.recipes.recipes) == 77


def test_every_recipe_has_a_whole_dish_amount_and_enough_steps(full_engine):
    doc = json.loads((DATA / "recipes_test.json").read_text(encoding="utf-8"))
    assert doc["collection_version"] == "RCP-0.2.0" and doc["status"] == "TEST ONLY"
    for r in doc["recipes"]:
        assert "per person" not in r["makes"] and len(r["steps"]) >= 2 and r["golden_rules_checked"] is True
        assert all(i["grams"] > 0 for i in r["ingredients"])
        assert all("FOOD-" in i["food"] for i in r["ingredients"])


def test_the_dish_names_and_aliases_are_not_shared(full_engine):
    seen = {}
    for rid, raw in full_engine.recipes.recipes.items():
        for alias in [raw["name"], *raw.get("aliases", [])]:
            key = alias.lower().split(" (")[0].strip()
            assert seen.setdefault(key, rid) == rid, (alias, seen[key], rid)
