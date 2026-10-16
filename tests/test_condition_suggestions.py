"""A person who says they have a health condition must get food and recipe suggestions, not an apology about an unknown food.

Found 16 Oct 2026: "I have obesity, so I need a healthy meal. What sabji would you recommend?" read "obesity" as a food at home,
told the person it was "left out" and sent the word to the dietitian queue.
"""
import json

import pytest

from edge_helpers import assert_valid

ASK = "i have obesity , so i need a healthy meal . what sabji would u recommend"
CONDITIONS = ["obesity", "obese", "overweight", "diabetes", "diabetic", "prediabetes", "hypertension", "thyroid", "pcos",
              "high blood pressure", "high sugar", "lose weight", "weight loss", "belly fat"]


def unknown_items(engine, conversation_id):
    path = engine.settings.audit_log
    out = []
    for line in open(path, encoding="utf-8"):
        rec = json.loads(line) if line.strip() else {}
        if rec.get("kind") == "unknown_item" and rec.get("conversation_id") == conversation_id:
            out.append(rec)
    return out


def ask(engine, text, profile=None):
    result = engine.answer(text, None, {"allergies": [], **(profile or {})})
    assert_valid(result, text)
    return result


def test_the_reported_message_gets_dishes_and_no_apology(engine):
    r = ask(engine, ASK)
    assert r.type == "recipe_request"
    assert "left out" not in r.answer and "obesity" not in r.answer.lower().split("here are")[0]
    assert r.answer.count("\n- ") >= 2 and "Which one should I show" in r.answer
    assert unknown_items(engine, r.conversation_id) == []


@pytest.mark.parametrize("condition", CONDITIONS)
def test_no_condition_phrase_is_ever_read_as_a_food(engine, condition):
    r = ask(engine, f"I have {condition}, so what sabji would you recommend?")
    assert "left out" not in r.answer and "isn't in our food list" not in r.answer, r.answer
    assert unknown_items(engine, r.conversation_id) == []
    assert "Here are some" in r.answer or "choose more often" in r.answer, r.answer


@pytest.mark.parametrize("text", ["I have obesity", "i am obese", "I have diabetes", "I have thyroid", "i have pcos"])
def test_a_condition_on_its_own_is_a_food_question_not_out_of_scope(engine, text):
    r = ask(engine, text)
    assert r.type in ({"food_choice_guidance", "status_dependent_protocol_question"} if "diabetes" in text else {"food_choice_guidance"}), (text, r.type)
    assert "I can only help with food" not in r.answer


def test_a_real_food_beside_a_condition_is_still_used(engine):
    r = ask(engine, "i have obesity and lauki, what can i cook")
    assert r.type == "meal_from_ingredients" and "lauki" in r.answer.lower() and "Lauki" in r.answer
    assert unknown_items(engine, r.conversation_id) == []


def test_an_unknown_food_beside_a_condition_is_still_caught_but_the_condition_is_not(engine):
    r = ask(engine, "i have obesity and zorblax, what can i cook")
    words = [x["word"] for x in unknown_items(engine, r.conversation_id)]
    assert "zorblax" in words and not any("obesity" in w for w in words), words


def test_the_condition_may_come_from_the_app_profile_alone(engine):
    with_profile = ask(engine, "suggest a healthy sabji", {"conditions": ["obesity"]})
    without = ask(engine, "suggest a healthy sabji")
    assert "Here are some light dishes" in with_profile.answer
    assert "Here are some light dishes" not in without.answer


def test_a_condition_said_earlier_in_the_chat_is_remembered(engine):
    first = engine.answer("I have obesity", None, {"allergies": []})
    second = engine.answer("what sabji should I eat?", first.conversation_id, {"allergies": []})
    assert "Here are some light dishes" in second.answer


@pytest.mark.parametrize("allergy,banned", [("milk", ["paneer", "ghee", "curd"]), ("peanut", ["peanut"]), ("egg", ["egg"])])
def test_dishes_for_a_condition_respect_allergies(engine, allergy, banned):
    r = engine.answer(ASK, None, {"allergies": [allergy]})
    listed = r.answer.lower().split("here are")[-1].split("kept in mind")[0]
    assert not any(b in listed for b in banned), listed


def test_dishes_offered_when_allergies_are_unknown_avoid_the_common_allergens(engine):
    r = engine.answer(ASK, None, {})
    listed = r.answer.lower().split("here are")[-1].split("kept in mind")[0]
    assert "paneer" not in listed and "egg" not in listed


def test_the_dish_listed_can_be_chosen_and_is_checked(engine):
    first = engine.answer(ASK, None, {"allergies": []})
    second = engine.answer("Bhindi Sabji", first.conversation_id, {"allergies": []})
    assert "**You need**" in second.answer and "bhindi" in second.answer.lower()


def test_an_emergency_beats_the_condition(engine):
    r = engine.answer("I have obesity and I have chest pain, what sabji should I eat", None, {"allergies": []})
    assert r.type == "emergency"


@pytest.mark.parametrize("text", [
    "i have obesity , so i need a healthy meal . what sabji would u recommend",
    "I HAVE OBESITY!!! recommend a sabji", "i have  obesity,what sabji", "main obese hoon, kya khaun", "I have obesity 😔 what to eat",
    "i have obesity i have obesity i have obesity", "I have obesity and I have diabetes, what sabji?"])
def test_odd_wordings_never_fail(engine, text):
    r = ask(engine, text)
    assert r.type in {"recipe_request", "food_choice_guidance", "meal_from_ingredients", "out_of_scope"}
    assert "isn't in our food list" not in r.answer or "obesity" not in r.answer


def test_a_message_naming_a_condition_and_no_food_never_reaches_the_dietitian_queue(engine):
    before = sum(1 for _ in open(engine.settings.audit_log, encoding="utf-8")) if engine.settings.audit_log.exists() else 0
    for c in CONDITIONS:
        engine.answer(f"I have {c}, what can I eat", None, {"allergies": []})
    new = [json.loads(line) for n, line in enumerate(open(engine.settings.audit_log, encoding="utf-8")) if n >= before and line.strip()]
    assert [r for r in new if r.get("kind") == "unknown_item"] == []


def test_the_programme_explanation_still_ends_with_the_medicines_question_and_dishes_follow_next_turn(engine):
    first = engine.answer("i have obesity . so what should i eat to reduce it", None, {})
    assert first.plan["followup"]["id"] == "FU-SAFETY" and "Here are some light dishes" not in first.answer
    second = engine.answer("No medicines. what sabji should I eat?", first.conversation_id, {"allergies": []})
    assert "Here are some light dishes" in second.answer or "**You need**" in second.answer
