"""Follow-ups that change the last search: 'except paneer' after a dish search, 'make it 400 kcal', 'what else', 'without onion' after recipes."""
import random
import re

import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from dixit_answer.state import ChatState
from tests.edge_helpers import assert_valid

NUTRITION = load_settings().nutrition_dir          # DIXIT_NUTRITION_DIR, as the app reads it
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")
NONVEG = {"allergies": [], "diet_type": "non_vegetarian"}
VEG = {"allergies": [], "diet_type": "vegetarian"}


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("refine"), dish_lookup=True), llm=MockLLM())


def chat(engine, *messages, profile=NONVEG):
    cid, out = None, []
    for message in messages:
        r = engine.answer(message, cid, profile)
        cid = r.conversation_id
        out.append(r)
    return out


def dishes(r):
    return [re.match(r"- (.+?):", l).group(1) for l in r.answer.split("\n") if re.match(r"- .+?: about", l)]


@needs_data
def test_except_paneer_after_a_dish_search_leaves_paneer_out(eng):
    first, second = chat(eng, "i want a meal with 326 kcal and 26 g protein", "except paneer", profile=VEG)
    assert second.type == "target_meal_search" and "I can only help" not in second.answer
    assert not any("paneer" in d.lower() for d in dishes(second)) and "without paneer" in second.answer


@needs_data
@pytest.mark.parametrize("follow", ["except chicken", "without chicken", "no chicken", "not chicken please", "other than chicken", "apart from chicken", "skip chicken",
                                    "and without chicken", "leave out chicken", "excluding chicken", "Except Chicken!"])
def test_the_ways_to_leave_something_out(eng, follow):
    first, second = chat(eng, "i want a meal with 40 g protein", follow)
    assert dishes(first) and any("chicken" in d.lower() for d in dishes(first))
    assert second.type == "target_meal_search" and not any("chicken" in d.lower() for d in dishes(second)), (follow, dishes(second))


@needs_data
def test_a_food_also_leaves_out_its_other_names(eng):
    r = chat(eng, "i want a meal with 20 g protein and 300 kcal", "without curd", profile=VEG)[1]
    assert not any(w in d.lower() for d in dishes(r) for w in ("curd", "dahi", "yogurt", "yoghurt"))


@needs_data
def test_what_else_gives_other_dishes_and_never_repeats(eng):
    r = chat(eng, "i want a meal with 40 g protein", "what else", "what else", "what else", "anything else", "another one")
    seen = []
    for item in r:
        for d in dishes(item):
            assert d not in seen, (d, seen)
            seen.append(d)
    assert len(seen) >= 6


@needs_data
def test_new_amounts_change_the_search_and_keep_what_was_left_out(eng):
    r = chat(eng, "i want a meal with 40 g protein", "without chicken", "make it 30 g protein instead", "and 400 kcal")
    assert "30 g of protein" in r[2].answer and "without chicken" in r[2].answer
    assert "400 calories" in r[3].answer and "30 g of protein" in r[3].answer and "without chicken" in r[3].answer
    assert not any("chicken" in d.lower() for d in dishes(r[3]))


@needs_data
def test_a_new_dish_search_forgets_the_old_leave_outs(eng):
    r = chat(eng, "i want a meal with 40 g protein", "without chicken", "i want a meal with 40 g protein")
    assert "without" not in r[2].answer.split("\n")[0]


@needs_data
def test_the_search_is_forgotten_when_the_topic_changes(eng):
    r = chat(eng, "i want a meal with 40 g protein", "calories in dal", "except paneer")
    assert r[2].type != "target_meal_search"
    again = chat(eng, "i want a meal with 40 g protein", "hello", "hello", "hello", "hello", "except chicken")
    assert again[-1].type == "target_meal_search"            # small talk keeps the search open (22 Oct: search memory, B) ...
    again = chat(eng, "i want a meal with 40 g protein", *["hello"] * 9, "except chicken")
    assert again[-1].type != "target_meal_search"            # ... for 8 turns, then it is forgotten


@needs_data
def test_diet_and_allergy_rules_still_apply_to_a_refined_search(eng):
    for r in chat(eng, "i want a meal with 40 g protein", "what else", "except paneer", "what else", profile={"allergies": ["milk"], "diet_type": "vegetarian"}):
        assert_valid(r)
        for d in dishes(r):
            assert not any(w in d.lower() for w in ("chicken", "fish", "mutton", "egg", "paneer", "prawn"))


@needs_data
@pytest.mark.parametrize("text", ["no thanks", "no", "not now", "ok", "thanks", "except", "without", "no more please", "except the", "yes"])
def test_replies_that_leave_nothing_out_do_not_start_a_search(eng, text):
    r = chat(eng, "i want a meal with 40 g protein", text)[1]
    assert_valid(r, text)
    assert "without" not in r.answer.split("\n")[0]


def test_an_old_saved_state_loads_without_the_search_fields():
    state = ChatState.from_json('{"turn": 4}', "c")
    assert state.last_targets == [] and state.target_exclude == [] and state.target_shown == [] and state.last_targets_turn == -99
    bad = ChatState.from_json('{"last_targets": "x", "target_shown": 5}', "c")
    assert bad.last_targets == [] and bad.target_shown == []


# ---------------------------------------------------------------- the same for recipes
def test_without_a_food_after_a_recipe_list_drops_the_dishes_that_use_it(full_engine):
    first, second, third = chat(full_engine, "i have aloo and paneer", "except onion", "no paneer", profile=VEG)
    assert "Aloo Pyaz ki Sabji" in first.answer and "Aloo Pyaz ki Sabji" not in second.answer
    assert second.type == "meal_from_ingredients" and "Aloo Gobhi" in second.answer
    assert "With aloo you can make" in third.answer and "Palak Paneer Bhurji" not in third.answer


def test_a_recipe_leave_out_with_nothing_before_it_is_not_guessed(full_engine):
    for text in ("except onion", "without onion", "no paneer"):
        assert chat(full_engine, text, profile=VEG)[0].type in {"out_of_scope", "food_allowed", "food_choice_guidance"}


def test_fuzz_refinements_always_give_a_valid_answer(eng):
    rng = random.Random(17)
    openers = ["i want a meal with 40 g protein", "meal with 300 kcal", "i have aloo and paneer", "calories in dal", "hello"]
    follow = ["except paneer", "without onion", "what else", "make it 30 g protein", "no", "no chicken", "and 200 kcal", "another one", "except", "not now",
              "without chicken and fish", "under 100 kcal", "at least 40 g protein"]
    for _ in range(100):
        for item in chat(eng, rng.choice(openers), *(rng.choice(follow) for _ in range(rng.randint(1, 4))), profile=rng.choice([VEG, NONVEG, None])):
            assert_valid(item)
