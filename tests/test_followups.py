"""A follow-up is read in the light of the last answer: "what else" after a recipe, "and protein?" after calories, "what about dal?"."""
import random
import re
from pathlib import Path

import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from dixit_answer.state import ChatState
from dixit_answer.understand import canonical_nutrient
from tests.edge_helpers import assert_valid

PROFILE = {"allergies": ["peanut"], "diet_type": "vegetarian"}
NUTRITION = Path(__file__).resolve().parents[2] / "Nutrition"
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("follow"), dish_lookup=True), llm=MockLLM())


def chat(engine, *messages, profile=PROFILE):
    cid, results = None, []
    for message in messages:
        out = engine.answer(message, cid, profile)
        cid = out.conversation_id
        results.append(out)
    return results


def dishes(answer):
    return re.findall(r"\*\*([^*]+)\*\*", answer.split("\n", 3)[2] if answer.count("\n") > 2 else answer)


# ---------------------------------------------------------------- "what else" after a recipe answer
def test_what_else_gives_a_different_dish_and_never_repeats(full_engine):
    first, second, third, fourth = chat(full_engine, "i have paneer", "what else", "what else", "anything else?")
    assert "Palak Paneer Bhurji" in first.answer and "Palak Paneer Bhurji" not in second.answer
    assert second.type == "meal_from_ingredients" and "Capsicum Paneer Sabji" in second.answer
    for later in (third, fourth):
        assert "Those are all the checked dishes" in later.answer
        assert "Palak Paneer Bhurji" not in later.answer and "Capsicum Paneer Sabji" not in later.answer


@pytest.mark.parametrize("more", ["what else", "What else?", "anything else", "something else", "any other", "another one", "more options", "show more",
                                  "something different", "one more", "any other dish"])
def test_the_many_ways_to_ask_for_more(full_engine, more):
    first, second = chat(full_engine, "i have paneer", more)
    assert second.type == "meal_from_ingredients" and "Palak Paneer Bhurji" not in second.answer, (more, second.answer[:150])
    assert "I can only help" not in second.answer


def test_more_after_a_list_skips_the_dishes_in_the_list(full_engine):
    first, second = chat(full_engine, "i have aloo", "what else", profile={"allergies": [], "diet_type": "vegetarian"})
    shown = set(re.findall(r"^- ([^(\n]+)", first.answer, re.M))
    assert shown and not any(name.strip() in second.answer.split("Kept in mind")[0] for name in shown if len(name.strip()) > 5), (shown, second.answer[:300])


def test_more_with_no_earlier_recipe_is_not_made_up(full_engine):
    first = chat(full_engine, "what else")[0]                      # since 22 Oct: a plain question back, not "I can only help"
    assert first.type == "more_unclear" and "not sure what to show more of" in first.answer and "With " not in first.answer
    after_hello = chat(full_engine, "hello", "what else")[1]
    assert after_hello.type == "more_unclear" and "With " not in after_hello.answer


def test_more_after_a_nutrient_question_is_not_a_recipe(full_engine):
    r = chat(full_engine, "calories in dal", "what else")[1]
    assert r.type != "meal_from_ingredients" and "With" not in r.answer.split("\n")[0]


def test_allergies_still_apply_to_more(full_engine):
    answers = chat(full_engine, "i have paneer", "what else", "what else", profile={"allergies": ["milk"], "diet_type": "vegetarian"})
    for r in answers:
        assert "Palak Paneer Bhurji" not in r.answer and "Capsicum Paneer Sabji" not in r.answer


def test_the_shown_list_belongs_to_one_conversation(full_engine):
    chat(full_engine, "i have paneer", "what else")
    fresh = chat(full_engine, "i have paneer")[0]
    assert "Palak Paneer Bhurji" in fresh.answer


# ---------------------------------------------------------------- "and protein?" and "what about dal?"
@needs_data
def test_and_protein_keeps_the_food(eng):
    first, second = chat(eng, "calories in dal", "and protein?")
    assert "dal" in second.answer.lower() and "protein" in second.answer and "calories" not in second.answer
    assert second.records == ["NUTR-INDB-ASC151"] or second.records


@needs_data
@pytest.mark.parametrize("follow", ["and protein?", "what about protein", "how about protein", "and its protein", "protein?", "what is the protein",
                                    "how much protein in it", "how much protein"])
def test_the_ways_to_ask_for_another_nutrient(eng, follow):
    r = chat(eng, "calories in dal", follow)[1]
    assert_valid(r, follow)
    assert "protein" in r.answer and "dal" in r.answer.lower(), (follow, r.answer[:120])


@needs_data
def test_what_about_another_food_asks_the_same_nutrient(eng):
    r = chat(eng, "how much protein in dal", "what about rice")[1]
    assert "rice" in r.answer.lower() and "protein" in r.answer


@needs_data
def test_what_about_another_food_after_can_i_eat(eng):
    r = chat(eng, "can i eat rice", "what about chapati")[1]
    assert "Chapati" in r.answer and "allowed" in r.answer


@needs_data
def test_a_dish_is_remembered_too(eng):
    r = chat(eng, "calories in poha", "and protein", profile=None)[1]
    assert "poha" in r.answer.lower() and "g of protein" in r.answer


@needs_data
def test_it_means_the_last_food(eng):
    r = chat(eng, "can i eat rice", "is it good for diabetes")[1]
    assert_valid(r)
    assert "I can only help" not in r.answer


@needs_data
def test_the_new_subject_replaces_the_old_one(eng):
    r = chat(eng, "calories in dal", "how about rice", "and carbs")[2]
    assert "rice" in r.answer.lower() and "carbohydrate" in r.answer


@needs_data
def test_a_follow_up_is_forgotten_after_a_few_turns(eng):
    r = chat(eng, "calories in dal", "hello", "hello", "hello", "hello", "and protein?")[-1]
    assert "dal" not in r.answer.lower().split("\n")[0] or "protein" not in r.answer


def test_a_follow_up_with_nothing_before_it_is_not_guessed(full_engine):
    for text in ("and protein?", "what about dal", "how many calories in it"):
        r = chat(full_engine, text)[0]
        assert_valid(r, text)


def test_a_message_that_names_its_own_food_is_left_alone(full_engine):
    r = chat(full_engine, "calories in dal", "calories in rice")[1]
    assert "rice" in r.answer.lower() and "dal" not in r.answer.lower()


def test_long_messages_are_not_rewritten(full_engine):
    long = "and what about protein because my doctor said i should eat more of it every day"
    r = chat(full_engine, "calories in dal", long)[1]
    assert_valid(r)


# ---------------------------------------------------------------- the helper and the state
@pytest.mark.parametrize("text, expected", [("how many calories", "calories"), ("kcal?", "calories"), ("carbs", "carbs"), ("Carbohydrates", "carbs"), ("protein", "protein"),
                                            ("fats", "fat"), ("fibre", "fibre"), ("fiber", "fibre"), ("hello", ""), ("proteinaceous", ""), ("", "")])
def test_canonical_nutrient(text, expected):
    assert canonical_nutrient(text) == expected


def test_an_old_saved_state_without_the_new_fields_still_loads():
    state = ChatState.from_json('{"turn": 3, "home_foods": ["FOOD-ALOO"]}', "c1")
    assert state.recipes_shown == [] and state.last_subject == "" and state.last_subject_turn == -99
    bad = ChatState.from_json('{"recipes_shown": "x", "last_subject": 5, "last_subject_turn": "no"}', "c2")
    assert bad.recipes_shown == [] and bad.last_subject == ""


def test_fuzz_follow_ups_always_give_a_valid_answer(full_engine):
    rng = random.Random(21)
    openers = ["i have paneer", "calories in dal", "can i eat rice", "hello", "i have aloo and onion", "protein in poha"]
    follow = ["what else", "and protein?", "what about", "how about rice", "it", "and", "?", "more", "why", "how many calories in it", "is it good", "that",
              "what about dal and rice", "and its fat", "anything else", "something different please", "और प्रोटीन", "kya aur"]
    for _ in range(120):
        r = chat(full_engine, rng.choice(openers), *(rng.choice(follow) for _ in range(rng.randint(1, 4))))
        for item in r:
            assert_valid(item)
