"""Foods that are not on the approved list: USDA facts for information, scaled to the grams asked, never a guess; and "can I eat X?" said plainly."""
import json
import random
import re

import pytest

from dixit_answer.config import load_settings
from dixit_answer.facts import Fact
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from dixit_answer.unknown_foods import food_phrase, grams_in, measure_in, scaled_fact, subject_of, variants
from tests.edge_helpers import assert_valid

NUTRITION = load_settings().nutrition_dir          # DIXIT_NUTRITION_DIR, as the app reads it
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")
VEG = {"allergies": ["peanut"], "diet_type": "vegetarian"}
NONVEG = {"allergies": [], "diet_type": "non_vegetarian"}


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("unknown"), dish_lookup=True), llm=MockLLM())


def ask(engine, text, profile=VEG):
    return engine.answer(text, None, profile)


# ---------------------------------------------------------------- the helpers
@pytest.mark.parametrize("text, expected", [("protein in 50 g chicken", 50.0), ("calories in 100g rice", 100.0), ("250 grams of dal", 250.0), ("2.5 gm paneer", 2.5),
                                            ("26 g protein", None), ("30 g of fat", None), ("500 ml milk", None), ("0 g rice", None), ("99999 g rice", None), ("no numbers", None)])
def test_grams_in(text, expected):
    assert grams_in(text) == expected


@pytest.mark.parametrize("text, expected", [("how much protein in 50 g chicken", "chicken"), ("calories in apple", "apple"), ("can i eat ice cream", "ice cream"),
                                            ("calories in a bowl of dal", "dal"), ("fibre in oats", "oats"), ("calories in dry", ""), ("calories in vegetarian", ""),
                                            ("how many calories", ""), ("", "")])
def test_food_phrase(text, expected):
    assert food_phrase(text) == expected


@pytest.mark.parametrize("text, expected", [("can i eat pizza", "pizza"), ("can i have ice cream", "ice cream"), ("is coffee ok", "coffee"), ("is pizza allowed", "pizza"),
                                            ("should i eat noodles", "noodles"), ("am i allowed to eat cake", "cake"), ("is it ok to eat maggi", "maggi"),
                                            ("can i eat fruits", ""), ("can i eat vegetables", ""), ("is it ok to eat after 8 pm", ""), ("can i eat at 9 pm", ""),
                                            ("should i eat to reduce weight", ""), ("can i eat what i want", ""), ("hello", ""), ("can i eat", "")])
def test_subject_of(text, expected):
    assert subject_of(text) == expected


def test_scaled_fact_is_a_plain_proportion():
    fact = Fact("USDA-1", "protein_g", 21.0, "about 21 g of protein per 100 g", "per 100 g", "USDA", "Chicken, raw", "21 g of protein")
    half = scaled_fact(fact, 50)
    assert half.value == 10.5 and half.serving == "per 50 g" and half.text == "about 10.5 g of protein per 50 g" and half.source == "USDA"
    kcal = scaled_fact(Fact("U", "energy_kcal", 165.0, "about 165 calories per 100 g", "per 100 g", "USDA", "x", ""), 30)
    assert kcal.value == 49.5 and kcal.amount == "50 calories"
    bowl = Fact("D", "energy_kcal", 139.0, "about 139 calories per bowl", "per bowl", "dev", "dal", "139 calories")
    assert scaled_fact(bowl, 50) is bowl and scaled_fact(Fact("U", "energy_kcal", None, "", "per 100 g", "USDA", "x"), 50).value is None


def test_variants_and_measures():
    assert variants("apple") == ["apple", "apples", "apples"] or "apples" in variants("apple")
    assert variants("apples")[1] == "apple" and variants("") == []
    assert measure_in("1 cup rice") == "cup" and measure_in("rice") is None


# ---------------------------------------------------------------- lookups
@needs_data
def test_grams_scale_the_usda_value_exactly(eng):
    r = ask(eng, "how much protein in 50 g chicken", NONVEG)
    assert r.type == "neutral_nutrient_lookup" and not r.fallback
    sentence = r.answer.split("\n")[0]
    value = float(re.search(r"has about ([\d.]+) g of protein", sentence).group(1))
    record = next(rec for rid, rec in eng.library.records.items() if rid in r.records)
    assert abs(value - record["nutrients_per_100g"]["protein_g"] * 0.5) < 0.06 and sentence.startswith("50 g of chicken")


@needs_data
def test_a_food_that_does_not_suit_gets_no_number(eng):
    for text, profile, word in (("protein in 50 g chicken", VEG, "Chicken"), ("calories in egg", VEG, "Egg"), ("calories in fish", {"diet_type": "jain"}, "Fish"),
                                ("calories in milk", {"allergies": ["milk"]}, "Milk")):
        r = ask(eng, text, profile)
        assert "won't suit you" in r.answer and word in r.answer and not r.records and "has about" not in r.answer, (text, r.answer[:120])


@needs_data
def test_usda_records_with_meat_or_allergens_in_their_names_are_never_shown(eng):
    for text in ("calories in biryani with rice", "calories in curry", "calories in soup", "calories in noodles", "calories in sandwich", "calories in salad"):
        r = ask(eng, text, VEG)
        low = r.answer.lower()
        assert not any(w in low for w in ("chicken", "beef", "pork", "fish", "meat", "shrimp", "tuna", "turkey", "egg")), (text, r.answer[:200])
        assert not re.search(r"peanut", low.split("kept in mind")[0]) or "won't suit" in low


@needs_data
def test_the_plain_food_comes_before_a_prepared_one(eng):
    for text, first in (("calories in apple", "apple"), ("fibre in oats", "oats"), ("calories in milk", "milk")):
        sentence = ask(eng, text, VEG).answer.split("\n")[0].lower()
        assert first in sentence and ("raw" in sentence or "whole" in sentence), sentence


@needs_data
def test_a_dish_word_that_is_also_a_usda_dish_still_asks_which_dish(eng):
    assert "Which one do you mean" in ask(eng, "calories in biryani", VEG).answer
    assert "Which one do you mean" in ask(eng, "calories in upma", VEG).answer


@needs_data
def test_sugar_is_what_is_asked_not_a_second_food(eng):
    r = ask(eng, "sugar in mango")
    assert r.type == "neutral_nutrient_lookup" and "mango" in r.answer and "g of sugar" in r.answer and "granulated" not in r.answer


@needs_data
def test_a_question_about_one_nutrient_of_a_food(eng):
    for text, nutrient in (("is dal high in protein", "protein"), ("is paneer rich in protein", "protein"), ("does rice have much fibre", "fibre"),
                           ("is ghee high in fat", "fat")):
        r = ask(eng, text, {"allergies": [], "diet_type": "vegetarian"})
        assert r.type == "neutral_nutrient_lookup", text
        assert nutrient in r.answer or "reliable" in r.answer, (text, r.answer[:120])


@needs_data
def test_a_piece_or_a_cup_gets_an_honest_line_and_a_dish_does_not(eng):
    assert "per-piece" in ask(eng, "calories in 3 idli").answer
    assert "a cup of rice" in ask(eng, "carbs in 1 cup rice").answer
    bowl = ask(eng, "calories in a bowl of dal").answer
    assert "per-piece" not in bowl and "reliable weight" not in bowl and "A bowl of dal" in bowl
    assert "50 g of" in ask(eng, "calories in 50 g rice").answer and "reliable weight" not in ask(eng, "calories in 50 g rice").answer


@needs_data
def test_nothing_found_says_so_and_queues_it(eng):
    r = ask(eng, "calories in zzqxfoo")
    assert "couldn't find" in r.answer and "zzqxfoo" in r.answer and not r.records
    lines = [json.loads(l) for l in eng.settings.audit_log.read_text(encoding="utf-8").splitlines()]
    assert any(l.get("kind") == "unknown_item" and l.get("word") == "zzqxfoo" for l in lines)


# ---------------------------------------------------------------- "can I eat X?"
@needs_data
@pytest.mark.parametrize("text, food", [("can i eat pizza", "pizza"), ("can i have ice cream", "ice cream"), ("can i drink tea", "tea"), ("is coffee ok", "coffee"),
                                        ("is noodles allowed", "noodles"), ("should i eat cake", "cake")])
def test_an_unlisted_food_is_not_judged_and_is_passed_on(eng, text, food):
    r = ask(eng, text)
    assert r.type == "unknown_food_question" and f"I can't say whether {food} fits the programme yet" in r.answer and "passed it to our dietitian" in r.answer
    assert "is allowed" not in r.answer and "is not allowed" not in r.answer and "should avoid" not in r.answer.lower()
    if "has about" in r.answer:
        assert "for information only, not permission" in r.answer
    assert_valid(r, text)
    lines = [json.loads(l) for l in eng.settings.audit_log.read_text(encoding="utf-8").splitlines()]
    assert any(l.get("kind") == "unknown_item" and l.get("word") == food for l in lines)


@needs_data
def test_unlisted_foods_that_do_not_suit_are_declined_not_judged(eng):
    for text in ("can i eat chicken", "can i have mutton curry", "is fish ok", "can i eat prawns"):
        r = ask(eng, text, VEG)
        assert "won't suit you" in r.answer and "passed it to our dietitian" not in r.answer and "has about" not in r.answer, (text, r.answer[:150])
    assert "won't suit you" in ask(eng, "can i have milk", {"allergies": ["milk"]}).answer


@needs_data
def test_kinds_of_food_and_times_are_not_unknown_foods(eng):
    for text in ("can i eat fruits", "can i eat vegetables", "is it ok to eat after 8 pm", "should i eat to reduce weight", "can i eat what i want"):
        r = ask(eng, text)
        assert r.type != "unknown_food_question", text
        assert "isn't on our approved food list" not in r.answer


@needs_data
def test_listed_foods_keep_their_own_answer(eng):
    for text in ("can i eat rice", "can i eat potato", "is paneer allowed", "can i have sugar"):
        assert ask(eng, text, {"allergies": [], "diet_type": "vegetarian"}).type != "unknown_food_question", text


@needs_data
def test_fuzz_unknown_food_questions_always_give_a_valid_answer(eng):
    rng = random.Random(6)
    foods = ["pizza", "tea", "chicken", "zzfoo", "ice cream", "fruits", "50 g", "apple", "milk", "8 pm", "{{F1}}", "%s", "😀", "poha", "to lose weight"]
    openers = ["can i eat", "is", "calories in", "protein in", "should i have", "how much fibre in", "can i drink"]
    tails = ["", " ok", " allowed", " good for me", "?"]
    for _ in range(250):
        text = f"{rng.choice(openers)} {rng.choice(foods)}{rng.choice(tails)}"
        assert_valid(ask(eng, text, rng.choice([VEG, NONVEG, None])), text)
