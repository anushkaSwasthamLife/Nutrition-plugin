"""The second round of the prompt review (21 Oct 2026): typos, ranges, who-are-you, body facts, special diets, building up what is at home, narrower diets."""
import random
import re

import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from dixit_answer.targets import parse_targets
from dixit_answer.understand import fix_typos
from tests.edge_helpers import assert_valid

NUTRITION = load_settings().nutrition_dir          # DIXIT_NUTRITION_DIR, as the app reads it
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")
VEG = {"allergies": ["peanut"], "diet_type": "vegetarian"}
NV = {"allergies": [], "diet_type": "non_vegetarian"}
VEG_OK = {"allergies": [], "diet_type": "vegetarian"}


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("review2"), dish_lookup=True), llm=MockLLM())


def chat(engine, *messages, profile=VEG):
    cid, out = None, []
    for message in messages:
        r = engine.answer(message, cid, profile)
        cid = r.conversation_id
        out.append(r)
    return out


def dishes(r):
    return [m.group(1) for m in (re.match(r"- (.+?): about", l) for l in r.answer.split("\n")) if m]


# ---------------------------------------------------------------- typos
@pytest.mark.parametrize("text, fixed", [("calries in poha", "calories in poha"), ("protien in dal", "protein in dal"), ("carbohidrate in rice", "carbohydrate in rice"),
                                         ("nutrtion of paneer", "nutrition of paneer"), ("vegetarain dishes", "vegetarian dishes"), ("potatos", "potatoes"),
                                         ("hello there", "hello there"), ("poha", "poha"), ("dal", "dal"), ("zzqxfoo", "zzqxfoo"), ("", "")])
def test_typos_are_repaired_but_other_words_are_left_alone(engine, text, fixed):
    assert fix_typos(text, engine.index) == fixed


@needs_data
def test_a_typo_still_gets_the_answer(eng):
    r = chat(eng, "protien in dal", profile=VEG_OK)[0]
    assert r.type == "neutral_nutrient_lookup" and "g of protein" in r.answer


# ---------------------------------------------------------------- amounts
@pytest.mark.parametrize("text, expected", [
    ("a meal between 300 and 400 calories", {("energy_kcal", 300.0, "range", 400.0)}),
    ("200 to 300 kcal", {("energy_kcal", 200.0, "range", 300.0)}), ("200 300 kcal", {("energy_kcal", 200.0, "range", 300.0)}),
    ("between 10 and 20 g protein", {("protein_g", 10.0, "range", 20.0)}), ("400 300 kcal", {("energy_kcal", 300.0, "about", None)}),
    ("under 300 calories", {("energy_kcal", 300.0, "max", None)})])
def test_ranges(text, expected):
    assert {(t.field, t.value, t.mode, t.high) for t in parse_targets(text)} == expected


@needs_data
def test_a_range_gives_dishes_inside_it(eng):
    r = chat(eng, "a meal between 300 and 400 calories", profile=NV)[0]
    assert r.type == "target_meal_search" and r.answer.startswith("Dishes with 300 to 400 calories")
    values = [int(re.search(r"about (\d+) calories", l).group(1)) for l in r.answer.split("\n") if "about" in l and l.startswith("- ")]
    assert values and all(300 <= v <= 400 for v in values)


@needs_data
@pytest.mark.parametrize("text", ["high protein under 300 calories", "need 40 g protein today", "i want 500 kcal meal", "dish with 30 grams of protein"])
def test_a_number_with_a_wish_or_a_need_is_a_dish_search(eng, text):
    r = chat(eng, text, profile=NV)[0]
    assert r.type == "target_meal_search", text
    if "under 300" in text:
        assert all(int(re.search(r"and (\d+) calories|about (\d+) calories", l).group(1) or 0) <= 300 for l in r.answer.split("\n") if l.startswith("- "))


# ---------------------------------------------------------------- who are you, body facts, special diets, goodbyes
@needs_data
@pytest.mark.parametrize("text", ["who are you", "what can you do", "help", "can you help me", "what is your name", "are you a doctor", "are you a real person", "who made you"])
def test_who_are_you(eng, text):
    r = chat(eng, text)[0]
    assert r.type == "about_assistant" and "not a doctor and not a person" in r.answer and "I can only help with" not in r.answer


@needs_data
@pytest.mark.parametrize("text", ["i am 25 years old", "my weight is 70 kg", "i am 160 cm tall", "i am a woman", "i weigh 80 kg", "my bmi is 27"])
def test_body_facts_are_thanked_and_no_plan_is_made(eng, text):
    r = chat(eng, text, profile=None)[0]
    assert r.type == "body_info" and "can't work out calorie needs" in r.answer


@needs_data
@pytest.mark.parametrize("text", ["i do keto", "i do intermittent fasting", "ekadashi fast food", "ramadan iftar ideas", "navratri vrat food", "i am on paleo"])
def test_special_diets_and_fasting_go_to_the_coach(eng, text):
    r = chat(eng, text, profile=None)[0]
    assert r.type == "special_diet" and "Dixit coach" in r.answer and not r.fallback


@needs_data
def test_goodbye_and_how_are_you(eng):
    assert chat(eng, "bye")[0].answer.startswith("Take care!")
    assert chat(eng, "hello how are you")[0].answer.startswith("Hello!")
    assert chat(eng, "good night")[0].type == "greeting_thanks"


@needs_data
def test_no_allergies_is_a_statement_that_is_kept(eng):
    first, second = chat(eng, "no allergies", "i have paneer and aloo", profile=None)
    assert first.type == "profile_statement" and first.answer == "Noted: no allergies."
    assert "common allergen" not in second.answer


# ---------------------------------------------------------------- what is at home builds up
@needs_data
def test_foods_said_later_are_added_to_what_is_at_home(eng):
    first, second, third, fourth = chat(eng, "i have aloo", "i also have onion", "and tomato", "what can i make", profile=VEG_OK)
    assert "With aloo you can make" in first.answer and "With aloo and onion" in second.answer
    assert "With aloo, onion and tomato" in third.answer and "With aloo, onion and tomato" in fourth.answer


@needs_data
def test_adding_a_food_only_works_straight_after_a_recipe_answer(eng):
    r = chat(eng, "calories in dal", "and tomato", profile=VEG_OK)[1]
    assert r.type != "meal_from_ingredients"


@needs_data
def test_can_i_have_is_not_i_have(eng):
    for text in ("can i have a cheat day", "can i have ice cream", "could i have dessert", "should i have sweets"):
        assert chat(eng, text)[0].type != "meal_from_ingredients", text


# ---------------------------------------------------------------- a narrower diet inside a dish search
@needs_data
def test_veg_inside_a_non_veg_search_gives_vegetarian_dishes_and_stays(eng):
    first, switched, more = chat(eng, "high protein food", "what about veg", "what else", profile=NV)
    assert any(w in " ".join(dishes(first)).lower() for w in ("chicken", "fish", "mutton", "prawn"))
    assert dishes(switched) and not any(w in " ".join(dishes(switched)).lower() for w in ("chicken", "fish", "mutton", "prawn", "egg"))
    assert not any(w in more.answer.lower().split("those are all")[0] for w in ("chicken", "mutton"))


@needs_data
def test_veg_in_the_first_request_is_honoured_even_for_a_non_veg_profile(eng):
    r = chat(eng, "high protein veg food", profile=NV)[0]
    assert dishes(r) and not any(w in " ".join(dishes(r)).lower() for w in ("chicken", "fish", "mutton", "prawn", "egg"))


@needs_data
def test_a_vegetarian_profile_is_never_widened_by_a_message(eng):
    r = chat(eng, "high protein non veg food", profile=VEG_OK)[0]
    assert "Your profile says vegetarian" in r.answer
    assert not any(w in " ".join(dishes(r)).lower() for w in ("chicken", "fish", "mutton", "prawn"))


# ---------------------------------------------------------------- foods in one or two words
@needs_data
@pytest.mark.parametrize("text", ["jalebi", "momos", "maggi", "poha upma", "pani puri", "vada pav"])
def test_bare_food_names_are_not_called_out_of_scope_when_we_hold_data_or_the_model_knows(eng, text):
    r = chat(eng, text)[0]
    assert_valid(r, text)
    assert r.type in {"neutral_nutrient_lookup", "out_of_scope"}, text


@needs_data
def test_a_bare_unknown_word_is_a_food_when_the_meaning_check_says_so(tmp_path):
    class Says(MockLLM):
        name = "fake"

        def classify_word(self, phrase, food_ids):
            return {"label": "food", "same_as": None} if phrase in {"jalebi", "momos"} else {"label": "not_food", "same_as": None}

        def reword(self, draft_text, user_message, max_words, failed_checks=None):
            return draft_text

    e = Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path, dish_lookup=True), llm=Says())
    assert e.answer("momos", None, VEG).type == "neutral_nutrient_lookup"
    assert e.answer("asdfghjkl", None, VEG).type == "out_of_scope"


# ---------------------------------------------------------------- foods asked about without "in"
@needs_data
@pytest.mark.parametrize("text, kind", [("dal protein", "neutral_nutrient_lookup"), ("idli kcal", "neutral_nutrient_lookup"), ("nutrition of paneer butter masala", "neutral_nutrient_lookup"),
                                        ("nutrition facts for oats", "neutral_nutrient_lookup"), ("kya paneer mein protein zyada hota hai", "neutral_nutrient_lookup"),
                                        ("is rice bad for diabetics", "personal_suitability"), ("is paneer good for weight loss", "personal_suitability"),
                                        ("are nuts allowed", "food_choice_guidance"), ("can i drink water with meals", "meal_protocol_application"),
                                        ("what should i eat first", "meal_protocol_application"), ("how long should a meal take", "meal_protocol_application"),
                                        ("i want to make khichdi", "recipe_request"), ("ingredients: aloo, gobhi", "meal_from_ingredients"),
                                        ("is honey allowed", "unknown_food_question"), ("can i have a cheat day", "food_choice_guidance")])
def test_phrasings_that_used_to_miss(eng, text, kind):
    assert chat(eng, text, profile=VEG_OK)[0].type == kind, text


@needs_data
def test_nutrition_facts_for_oats_names_oats_not_facts(eng):
    r = chat(eng, "nutrition facts for oats", profile=VEG_OK)[0]
    assert "oats" in r.answer.lower() and "facts" not in r.answer.lower()


@needs_data
def test_a_black_coffee_is_coffee(eng):
    r = chat(eng, "calories in black coffee", profile=VEG_OK)[0]
    assert "coffee" in r.answer.lower() and "black rice" not in r.answer.lower() and "has about" in r.answer


@needs_data
def test_pani_puri_is_not_water(eng):
    assert "cumin" not in chat(eng, "pani puri", profile=VEG_OK)[0].answer.lower()


@needs_data
def test_two_foods_in_one_question_are_both_answered(eng):
    r = chat(eng, "can i eat roti and dal for dinner", profile=VEG_OK)[0]
    assert "Chapati is allowed" in r.answer and "Dal is allowed" in r.answer


@needs_data
def test_one_calorie_is_singular(eng):
    from dixit_answer.unknown_foods import scaled_fact
    from dixit_answer.facts import Fact
    fact = scaled_fact(Fact("U", "energy_kcal", 100.0, "x", "per 100 g", "USDA", "x"), 1)
    assert fact.amount == "1 calorie"


@needs_data
def test_fuzz_second_round_prompts_never_break(eng):
    rng = random.Random(77)
    pieces = ["calries", "protien", "between 200 and 300 kcal", "need 20 g protein", "who are you", "i am 30 years old", "keto", "bye", "no allergies", "and tomato",
              "i also have onion", "what about veg", "what can i make", "jalebi", "black coffee", "pani puri", "can i have a cheat day", "high protein veg food", "except paneer",
              "!!!", "😀", "{{F1}}", "%s", "dal protein", "is honey allowed"]
    for _ in range(300):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(1, 3)))
        for item in chat(eng, text, rng.choice(pieces), profile=rng.choice([VEG, NV, None, VEG_OK])):
            assert_valid(item, text)
