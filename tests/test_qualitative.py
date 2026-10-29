"""'high protein non veg food, what can I have': a wish for a lot or a little of a nutrient lists dishes, never an unrelated dish."""
import random
import re

import pytest

from dixit_answer.config import load_settings
from dixit_answer.targets import MEAT_FISH, is_target_request, parse_qualifiers
from tests.edge_helpers import assert_valid

NUTRITION = load_settings().nutrition_dir          # DIXIT_NUTRITION_DIR, as the app reads it
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine

VEG = {"allergies": ["peanut"], "diet_type": "vegetarian"}
NONVEG = {"allergies": [], "diet_type": "non_vegetarian"}


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("qual"), dish_lookup=True), llm=MockLLM())


def ask(engine, text, profile=VEG):
    return engine.answer(text, None, profile)


def dish_lines(answer):
    return [line for line in answer.split("\n") if re.match(r"- .+?: about", line)]


@pytest.mark.parametrize("text, expected", [
    ("high protein food", {("protein_g", "high")}), ("low carb dinner ideas", {("carb_g", "low")}), ("low calorie snacks", {("energy_kcal", "low")}),
    ("something light for dinner", {("energy_kcal", "low")}), ("foods rich in fibre", {("fibre_g", "high")}), ("protein rich meal", {("protein_g", "high")}),
    ("i want to eat less fat", {("fat_g", "low")}), ("which dish has the most protein", {("protein_g", "high")}), ("lowest calorie dish", {("energy_kcal", "low")}),
    ("high protein low carb", {("protein_g", "high"), ("carb_g", "low")}), ("meal high in fibre", {("fibre_g", "high")}), ("fibre-rich breakfast", {("fibre_g", "high")}),
    ("hello", set()), ("is dal high", set()), ("what is the weather", set()), ("rich", set())])
def test_wishes_are_read(text, expected):
    assert {(t.field, t.mode) for t in parse_qualifiers(text.replace("-", " "))} == expected


@needs_data
def test_the_screenshot_question_lists_non_veg_high_protein_dishes(eng):
    r = ask(eng, "i have to eat high protein non veg food , what can i have", NONVEG)
    assert r.type == "target_meal_search" and "Pearl millet" not in r.answer and r.answer.startswith("Non-veg dishes with high protein")
    lines = dish_lines(r.answer)
    assert 1 <= len(lines) <= 3 and any(w in " ".join(lines).lower() for w in MEAT_FISH)
    assert not [c for c in r.checks if not c["passed"]]


@needs_data
@pytest.mark.parametrize("text", ["high protein food", "what are high protein foods", "high protein veg food", "i need a protein rich meal", "protein rich food for dinner",
                                  "which dish has the most protein"])
def test_a_vegetarian_gets_only_vegetarian_high_protein_dishes(eng, text):
    r = ask(eng, text)
    assert r.type == "target_meal_search"
    assert not set(re.findall(r"[a-z]+", " ".join(dish_lines(r.answer)).lower())) & (MEAT_FISH | {"egg", "eggs"})
    for line in dish_lines(r.answer):
        assert float(re.search(r"about ([\d.]+) g of protein", line).group(1)) >= 15


@needs_data
def test_a_non_veg_wish_with_a_vegetarian_profile_says_what_was_done(eng):
    r = ask(eng, "i want high protein non veg food", VEG)
    assert "Your profile says vegetarian" in r.answer
    assert not set(re.findall(r"[a-z]+", " ".join(dish_lines(r.answer)).lower())) & MEAT_FISH


@needs_data
def test_the_dishes_meet_the_line_for_what_was_asked(eng):
    low_carb = ask(eng, "low carb dinner ideas", NONVEG)
    for line in dish_lines(low_carb.answer):
        carbs, kcal = (float(x) for x in re.search(r"about ([\d.]+) g of carbohydrate and (\d+) calories", line).groups())
        assert carbs * 4 / kcal <= 0.25 and kcal >= 80
    fibre = ask(eng, "high fibre food", NONVEG)
    assert all(float(re.search(r"about ([\d.]+) g of fibre", l).group(1)) >= 5 for l in dish_lines(fibre.answer))
    light = ask(eng, "something light", NONVEG)
    assert all(40 <= int(re.search(r"about (\d+) calories", l).group(1)) <= 150 for l in dish_lines(light.answer))


@needs_data
def test_the_most_comes_first(eng):
    values = [float(re.search(r"about ([\d.]+) g of protein", l).group(1)) for l in dish_lines(ask(eng, "high protein food", NONVEG).answer)]
    assert values == sorted(values, reverse=True) and len(values) == 3


@needs_data
def test_drinks_and_spoonfuls_are_not_offered_as_meals(eng):
    for text in ("low fat food", "low calorie dish", "something light"):
        for line in dish_lines(ask(eng, text, NONVEG).answer):
            assert not re.search(r"per (glass|tall glass|cup|tea cup|tablespoon|teaspoon|jar|biscuit|cookie)", line), line
    assert ask(eng, "low calorie drink", NONVEG).type == "target_meal_search"


@needs_data
def test_a_snack_request_says_the_programme_has_no_snacks(eng):
    r = ask(eng, "low calorie snacks")
    assert "no snacks between meals" in r.answer


@needs_data
def test_a_wish_with_a_food_in_it_keeps_its_normal_answer(eng):
    for text in ("is dal high in protein", "is paneer rich in protein", "how much protein in dal", "calories in poha"):
        assert ask(eng, text).type != "target_meal_search", text


@needs_data
def test_allergy_and_follow_ups_work_with_wishes(eng):
    cid = None
    for text in ("high protein food", "except paneer", "what else"):
        r = eng.answer(text, cid, {"allergies": ["milk"], "diet_type": "vegetarian"})
        cid = r.conversation_id
        assert_valid(r, text)
        assert not any(w in l.lower() for l in dish_lines(r.answer) for w in ("paneer", "milk", "curd", "cheese"))


@needs_data
def test_fuzz_wishes_always_give_a_valid_answer(eng):
    rng = random.Random(2)
    vocab = ["high", "low", "protein", "carb", "fat", "fibre", "calorie", "rich", "light", "food", "dish", "meal", "veg", "non", "veg", "most", "less", "and",
             "what", "can", "i", "have", "dinner", "snack", "drink", "{{F1}}", "%s", "?", "paneer", "dal", "in", "is"]
    for _ in range(250):
        text = " ".join(rng.choice(vocab) for _ in range(rng.randint(1, 9)))
        assert_valid(ask(eng, text, rng.choice([VEG, NONVEG, None])), text)


def test_a_number_and_a_wish_together():
    targets = is_target_request("high protein meal under 400 kcal")
    assert {(t.field, t.mode) for t in targets} == {("energy_kcal", "max"), ("protein_g", "high")}
