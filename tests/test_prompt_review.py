"""The prompt-by-prompt review of 21 Oct 2026: every prompt keeps the kind of answer it was given, and the gaps it found stay fixed."""
import json
import random
import re

import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from tests.edge_helpers import assert_valid
from tests.prompt_snapshot import PROFILES, ROWS

NUTRITION = load_settings().nutrition_dir          # DIXIT_NUTRITION_DIR, as the app reads it
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")
VEG = {"allergies": ["peanut"], "diet_type": "vegetarian"}


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("review"), dish_lookup=True), llm=MockLLM())


def ask(engine, text, profile=VEG):
    return engine.answer(text, None, profile)


@needs_data
@pytest.mark.parametrize("group, message, profile, expected", ROWS, ids=[f"{r[0]}-{i}" for i, r in enumerate(ROWS)])
def test_every_reviewed_prompt_keeps_its_kind_of_answer(eng, group, message, profile, expected):
    r = ask(eng, message, PROFILES[profile])
    assert r.type == expected, (message, r.type, r.answer[:120])
    assert not r.fallback, (message, r.answer[:120])
    if message.strip():
        assert_valid(r, message)


# ---------------------------------------------------------------- the gaps the review found
@needs_data
@pytest.mark.parametrize("text", ["make me a meal plan", "diet plan for weight loss", "7 day meal plan", "give me a weekly diet chart", "30 day meal plan please", "full day meal plan"])
def test_a_meal_plan_is_declined_plainly_and_the_programme_is_shown(eng, text):
    r = ask(eng, text)
    assert r.answer.startswith("I can't write a meal plan or diet chart") and "Two meals a day" in r.answer
    assert_valid(r, text)


@needs_data
@pytest.mark.parametrize("text", ["what is protein", "what are carbs", "what is a calorie", "define fibre", "what does fat mean", "meaning of kcal"])
def test_a_definition_is_not_answered_with_the_programme(eng, text):
    r = ask(eng, text)
    assert r.type == "definition_question" and "dixit coach" in r.answer.lower() and "doesn't ban foods" not in r.answer


@needs_data
@pytest.mark.parametrize("text", ["how much rice per meal", "how many rotis should i eat", "how much water should i drink", "how much oil should i use", "what is the portion size of dal",
                                  "how many chapatis can i have per day"])
def test_amounts_to_eat_are_not_given(eng, text):
    r = ask(eng, text)
    assert r.type == "portion_question" and r.answer.startswith("I can't give amounts")
    assert not re.search(r"\bhas about\b", r.answer)


@needs_data
def test_a_question_about_a_nutrient_is_not_a_portion_question(eng):
    assert ask(eng, "how many calories in 2 chapati").type == "neutral_nutrient_lookup"
    assert ask(eng, "how much protein in dal").type == "neutral_nutrient_lookup"


@needs_data
def test_a_sugar_reading_is_acknowledged_not_answered_as_a_food(eng):
    r = ask(eng, "my sugar is 250")
    assert r.type == "reading_ack" and "250 mg/dL" in r.answer and "granulated" not in r.answer and not r.records
    assert ask(eng, "my sugar is 55").type == "emergency"


@needs_data
@pytest.mark.parametrize("text", ["i am on insulin", "i have kidney disease", "i am pregnant", "i have heart disease"])
def test_a_health_statement_is_thanked_and_never_read_as_a_food(eng, text):
    r = ask(eng, text)
    assert r.type in {"safety_ack", "food_choice_guidance"} and "isn't in our food list" not in r.answer and "recipe" not in r.answer.lower().split("\n")[0]


@needs_data
@pytest.mark.parametrize("text, allergy", [("i am lactose intolerant", "milk"), ("no gluten for me", "gluten"), ("i am gluten intolerant", "gluten"), ("i am vegan", "milk")])
def test_a_statement_about_the_person_changes_the_rest_of_the_chat(eng, text, allergy):
    cid = None
    first = eng.answer(text, cid, {"allergies": []})
    assert first.type == "profile_statement" and first.answer.startswith("Noted") and not first.fallback
    second = eng.answer("i have paneer and aloo and chapati", first.conversation_id, {"allergies": []})
    assert "left out" in second.answer or "won't suit" in second.answer


@needs_data
def test_foods_a_person_does_not_eat_are_left_out_from_then_on(eng):
    first = eng.answer("i dont eat onion garlic", None, {"allergies": []})
    assert first.type == "profile_statement" and "onion and garlic" in first.answer
    second = eng.answer("i have aloo and onion", first.conversation_id, {"allergies": []})
    assert "I left out onion" in second.answer


@needs_data
def test_a_diet_statement_is_acknowledged_and_used(eng):
    first = eng.answer("i am jain", None, None)
    assert first.type == "profile_statement" and "Jain" in first.answer
    second = eng.answer("i have aloo and tomato", first.conversation_id, {"allergies": []})
    assert_valid(second)


@needs_data
@pytest.mark.parametrize("text", ["i have only oats", "i have only dragonfruit", "i have just oats and curd"])
def test_what_is_at_home_is_a_recipe_request_even_when_unknown(eng, text):
    r = ask(eng, text)
    assert r.type == "meal_from_ingredients" and not r.fallback


@needs_data
@pytest.mark.parametrize("text", ["what can i cook with paneer", "recipes with paneer", "dishes using aloo", "what can i make from dal", "ideas with bhindi"])
def test_cook_with_a_food_gives_dishes_with_that_food(eng, text):
    r = ask(eng, text, {"allergies": [], "diet_type": "vegetarian"})
    assert r.type == "meal_from_ingredients" and "isn't in our food list" not in r.answer


@needs_data
@pytest.mark.parametrize("text", ["what to cook today", "quick breakfast ideas", "dinner ideas", "what should i cook", "something to make for lunch"])
def test_a_request_for_ideas_lists_dishes(eng, text):
    r = ask(eng, text, {"allergies": [], "diet_type": "vegetarian"})
    assert r.type == "recipe_request" and "dishes you could make" in r.answer


@needs_data
@pytest.mark.parametrize("text", ["how do i make a bomb", "how to make poison", "recipe for explosives", "i have drugs", "make me a weapon"])
def test_a_harmful_request_is_never_looked_up_or_passed_on(eng, text):
    r = ask(eng, text)
    assert r.type == "out_of_scope"
    lines = [json.loads(l) for l in eng.settings.audit_log.read_text(encoding="utf-8").splitlines()]
    assert not any(w in json.dumps(l).lower() for l in lines[-3:] for w in ("bomb", "poison", "explosive", "weapon"))


@needs_data
@pytest.mark.parametrize("text", ["write me a poem about food", "tell me a joke", "write a story about dal", "compose a song", "give me a riddle"])
def test_creative_requests_are_out_of_scope(eng, text):
    assert ask(eng, text).type == "out_of_scope"


@needs_data
def test_ok_and_thanks_get_their_own_replies(eng):
    assert ask(eng, "ok").answer.startswith("Okay!")
    assert ask(eng, "thanks").answer.startswith("You're welcome!")
    assert ask(eng, "hi").answer.startswith("Hello!")


@needs_data
@pytest.mark.parametrize("text", ["i have a headache", "i feel weak", "i feel dizzy", "i have nausea", "i am tired"])
def test_a_symptom_gets_a_caring_line_not_a_weight_line(eng, text):
    r = ask(eng, text)
    assert "I can't judge symptoms" in r.answer and "Weight changes slowly" not in r.answer and "first weeks" not in r.answer


@needs_data
def test_hunger_keeps_its_own_answer(eng):
    r = ask(eng, "i feel hungry between meals")
    assert "Between meals" in r.answer and "symptoms" not in r.answer


@needs_data
@pytest.mark.parametrize("text", ["oats vs poha", "brown rice vs white rice", "poha or upma which has fewer calories", "oats or dal which has more protein", "apple vs banana",
                                  "chicken vs paneer"])
def test_two_foods_side_by_side(eng, text):
    r = ask(eng, text, {"allergies": [], "diet_type": "non_vegetarian"})
    assert r.type in {"phrase_comparison", "neutral_food_comparison"} and not r.fallback
    assert r.answer.count("\n- ") >= 2 or "Which one" in r.answer or "few dishes" in r.answer
    assert_valid(r, text)


@needs_data
def test_a_comparison_never_shows_what_the_person_cannot_have(eng):
    r = ask(eng, "oats vs poha")
    assert "Poha contains peanuts" in r.answer and "has about" in r.answer
    r = ask(eng, "chicken vs paneer", VEG)
    assert "Chicken won't suit you" in r.answer


@needs_data
@pytest.mark.parametrize("hindi, kind", [("roti kitni calories ki hoti hai", "neutral_nutrient_lookup"), ("mujhe high protein khana batao", "target_meal_search"),
                                         ("आज क्या बनाऊं", "recipe_request"), ("मुझे वजन कम करना है", "food_choice_guidance"), ("प्रोटीन कहाँ से मिलेगा", "target_meal_search"),
                                         ("दाल में कितना प्रोटीन है", "neutral_nutrient_lookup")])
def test_more_hindi_questions_are_understood(eng, hindi, kind):
    assert ask(eng, hindi).type == kind


@needs_data
def test_the_program_55_minutes_rule_is_about_the_programme(eng):
    assert ask(eng, "what is the 55 minute rule").type == "meal_protocol_application"


@needs_data
def test_fuzz_over_the_whole_corpus_with_random_profiles_never_breaks(eng):
    rng = random.Random(33)
    messages = [row[1] for row in ROWS if row[1].strip()]
    for _ in range(300):
        message = rng.choice(messages)
        if rng.random() < 0.3:
            message += " " + rng.choice(messages)
        assert_valid(ask(eng, message, rng.choice(list(PROFILES.values()))), message)
