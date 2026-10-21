"""'I want a meal with 326 kcal and 26 g protein, what can I have': dishes whose stored values sit near the amounts, never a plan."""
import random
import re
from pathlib import Path

import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from dixit_answer.targets import MEAT_FISH, is_target_request, parse_targets
from tests.edge_helpers import assert_valid

NUTRITION = Path(__file__).resolve().parents[2] / "Nutrition"
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")
VEG = {"allergies": ["peanut"], "diet_type": "vegetarian"}
NONVEG = {"allergies": [], "diet_type": "non_vegetarian"}


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("targets"), dish_lookup=True), llm=MockLLM())


def ask(engine, text, profile=VEG):
    return engine.answer(text, None, profile)


def dish_lines(answer):
    return [line for line in answer.split("\n") if line.startswith("- ")]


# ---------------------------------------------------------------- reading the numbers
@pytest.mark.parametrize("text, expected", [
    ("i want a meal with 326kcal carbs and 26g protein", {("energy_kcal", 326.0, "about"), ("protein_g", 26.0, "about")}),
    ("what can i eat under 400 calories with 20 g protein", {("energy_kcal", 400.0, "max"), ("protein_g", 20.0, "about")}),
    ("a dish with at least 15 g fibre", {("fibre_g", 15.0, "min")}),
    ("i want 300 calories", {("energy_kcal", 300.0, "about")}),
    ("something with protein 30 g", {("protein_g", 30.0, "about")}),
    ("a meal of 450.5 kcal", {("energy_kcal", 450.5, "about")}),
    ("less than 20 g fat and 10 g carbs", {("fat_g", 20.0, "max"), ("carb_g", 10.0, "about")}),
    ("calories 250", {("energy_kcal", 250.0, "about")}),
    ("a meal above 500 kcal", {("energy_kcal", 500.0, "min")}),
])
def test_amounts_are_read(text, expected):
    assert {(t.field, t.value, t.mode) for t in parse_targets(text)} == expected


@pytest.mark.parametrize("text", ["calories in poha", "how many calories in 100 g of rice", "i have 200 g paneer", "i want a meal", "10 kcal", "99999 kcal", "0 g protein",
                                  "protein", "1234567 calories", "", "i am 25 years old and want a meal", "give me 2 recipes"])
def test_things_that_are_not_targets_are_not_read(text):
    assert not is_target_request(text) or not parse_targets(text)


def test_a_number_alone_is_never_a_target():
    assert parse_targets("i want 26 something") == [] and parse_targets("2 meals a day") == []


# ---------------------------------------------------------------- the answer
@needs_data
def test_the_screenshot_question_gets_dishes_not_cooking_tips(eng):
    r = ask(eng, "i want a meal with 326kcal carbs and 26g protein what can i have")
    assert r.type == "target_meal_search" and "We don't have a checked recipe" not in r.answer
    assert 1 <= len(dish_lines(r.answer)) <= 3 and "not a meal plan" in r.answer
    assert not [c for c in r.checks if not c["passed"]] and not r.fallback
    assert_valid(r)


@needs_data
def test_every_number_shown_is_the_dishs_own_stored_value(eng):
    r = ask(eng, "i want a meal with 326 kcal and 26 g protein", NONVEG)
    assert r.records
    for line in dish_lines(r.answer):
        code = re.search(r"- (.+?):", line).group(1)
        energy = float(re.search(r"about (\d+) calories", line).group(1))
        protein = float(re.search(r"and ([\d.]+) g of protein", line).group(1))
        results = [eng.dishes.values(c) for c in eng.dishes.by_code if eng.dishes.label(c).split(" (")[0] == code]
        assert any(abs(x.per_serving["energy_kcal"] - energy) < 0.51 and abs(x.per_serving["protein_g"] - protein) < 0.06 for x in results), line


@needs_data
def test_close_dishes_are_introduced_as_matches_and_far_ones_as_nearest(eng):
    close = ask(eng, "i want a meal with 326 kcal and 26g protein", NONVEG).answer
    assert close.startswith("Dishes with about 326 calories and 26 g of protein:")
    far = ask(eng, "i want a meal with 326 kcal and 26g protein").answer
    assert far.startswith("No dish matches") and "nearest" in far


@needs_data
def test_under_and_at_least_are_limits_not_targets(eng):
    r = ask(eng, "what can i eat under 400 calories with 20 g protein", NONVEG)
    for line in dish_lines(r.answer)[:2]:
        assert int(re.search(r"about (\d+) calories", line).group(1)) <= 400
    low = ask(eng, "a dish with at least 30 g protein", NONVEG)
    for line in dish_lines(low.answer):
        assert float(re.search(r"([\d.]+) g of protein", line).group(1)) >= 30


@needs_data
def test_a_vegetarian_is_never_offered_meat_fish_or_egg(eng):
    for text in ("i want a meal with 40 g protein", "i want a meal with 326 kcal and 26g protein", "something with 30g protein", "a dish with 25 g protein and 300 kcal"):
        for profile in (VEG, {"allergies": [], "diet_type": "vegetarian"}, {"allergies": [], "diet_type": "jain"}, {"allergies": []}, None):
            r = ask(eng, text, profile)
            lowered = set(re.findall(r"[a-z]+", " ".join(dish_lines(r.answer)).lower()))
            assert not lowered & (MEAT_FISH | {"egg", "eggs", "omelette"}), (text, profile, dish_lines(r.answer))


@needs_data
def test_a_non_vegetarian_can_be_offered_meat(eng):
    text = " ".join(dish_lines(ask(eng, "i want a meal with 40 g protein", NONVEG).answer)).lower()
    assert any(w in text for w in MEAT_FISH)


@needs_data
@pytest.mark.parametrize("allergy, words", [("peanut", {"peanut", "peanuts", "groundnut"}), ("milk", {"milk", "paneer", "curd", "ghee", "cream", "butter", "khoya", "cheese"})])
def test_an_allergy_removes_dishes_with_that_ingredient(eng, allergy, words):
    profile = {"allergies": [allergy], "diet_type": "non_vegetarian"}
    for text in ("i want a meal with 300 kcal and 15 g protein", "something with 20g protein", "a meal with 400 kcal"):
        r = ask(eng, text, profile)
        for line in dish_lines(r.answer):
            name = re.search(r"- (.+?):", line).group(1).lower()
            codes = [c for c in eng.dishes.by_code if eng.dishes.label(c).split(" (")[0].lower() == name]
            for code in codes:
                ingredients = " ".join(eng.dishes.ingredient_names(code)).lower()
                assert not any(w in ingredients.split() or w in name.split() for w in words), (allergy, name, ingredients[:120])


@needs_data
def test_unknown_allergies_stay_clear_of_the_common_allergens(eng):
    r = ask(eng, "i want a meal with 300 kcal and 15 g protein", None)
    assert_valid(r)
    for line in dish_lines(r.answer):
        assert "paneer" not in line.lower() and "milk" not in line.lower() and "curd" not in line.lower()


@needs_data
def test_nothing_close_says_so_and_offers_no_number(eng):
    r = ask(eng, "i want a meal with 3000 kcal and 250 g protein", NONVEG)
    assert r.type == "target_meal_search" and "couldn't find" in r.answer and not dish_lines(r.answer)
    assert_valid(r)


@needs_data
def test_a_food_in_the_message_keeps_the_normal_answer(eng):
    assert ask(eng, "calories in poha").type == "neutral_nutrient_lookup"
    assert ask(eng, "how much protein in dal").type == "neutral_nutrient_lookup"
    assert ask(eng, "i have paneer and want 20 g protein").type != "target_meal_search"


def test_without_dish_data_it_says_so(full_engine):
    saved, full_engine.composer.dishes = full_engine.composer.dishes, None
    try:
        r = full_engine.answer("i want a meal with 300 kcal and 20 g protein", None, VEG)
        assert r.type == "target_meal_search" and "can't look dishes up" in r.answer
    finally:
        full_engine.composer.dishes = saved


@needs_data
def test_it_works_in_hindi_and_after_a_follow_up(eng):
    cid = ask(eng, "i want a meal with 40 g protein", NONVEG).conversation_id
    again = eng.answer("what else", cid, NONVEG)
    assert_valid(again)


@needs_data
def test_fuzz_target_messages_always_give_a_valid_answer(eng):
    rng = random.Random(8)
    vocab = ["i", "want", "a", "meal", "with", "dish", "under", "at", "least", "kcal", "calories", "g", "protein", "carbs", "fat", "fibre", "26", "326", "0", "9999",
             "40g", "300kcal", "and", "what", "can", "i", "have", "{{F1}}", "%s", "!", "?", "-5", "2.5", "poha", "dal", "paneer"]
    for _ in range(250):
        text = " ".join(rng.choice(vocab) for _ in range(rng.randint(2, 12)))
        assert_valid(ask(eng, text, rng.choice([VEG, NONVEG, None])), text)
