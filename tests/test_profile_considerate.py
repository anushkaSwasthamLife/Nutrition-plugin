"""What a person ticks (allergies, health conditions, diet) must be respected in every answer, and the answer must say so.

Found 16 Oct 2026: a gluten-allergic person asking "what sabji goes with chapati?" got "I can't give a reliable answer": the approved
label "closer to the chapati or rice part of the meal" named a food they cannot have. Answers also never showed that the profile was used.
"""
import itertools

import pytest

from dixit_answer.person import Person, build_person
from dixit_answer.state import ChatState
from edge_helpers import assert_valid, lines_naming_blocked_foods

ALL = ["milk", "peanut", "tree_nut", "gluten", "egg", "chickpea", "soy", "sesame"]
QUESTIONS = ["what can I eat for dinner?", "what sabji can I eat?", "I have lauki and tomato, what can I cook?", "give me recipes",
             "suggest a healthy sabji", "how do I make palak paneer?", "can I have curd?", "can I eat chapati?",
             "how many carbs in one chapati?", "what should I eat to reduce weight", "I have chapati, what sabji can I eat?",
             "which sabji is best?", "can I have almonds?", "I have egg and onion, what can I cook", "what can I snack on", "I feel hungry",
             "I have capsicum, tofu and rajma, what can I cook?"]
PROFILES = [[a] for a in ALL] + [["milk", "egg"], ["gluten", "chickpea", "soy"], ALL]
CONDITIONS = [[], ["obesity"], ["hypertension", "diabetes"]]


def profile(allergies, conditions=(), **extra):
    return {"allergies": list(allergies), "conditions": list(conditions), **extra}


@pytest.mark.parametrize("allergies,conditions", list(itertools.product(PROFILES, CONDITIONS)))
def test_no_question_ever_falls_back_or_names_a_food_the_person_cannot_have(engine, allergies, conditions):
    prof = profile(allergies, conditions)
    blocked = engine.allergy.blocked_foods(build_person(prof, ChatState("x"), engine.index, engine.allergy))
    for q in QUESTIONS:
        r = engine.answer(q, None, prof)
        assert_valid(r, q)
        assert not r.fallback and "reliable answer" not in r.answer, (allergies, conditions, q, [c["detail"] for c in r.checks if not c["passed"]])
        assert lines_naming_blocked_foods(engine, r.answer, blocked) == [], (allergies, conditions, q)


def test_the_gluten_chapati_case_that_was_reported(engine):
    r = engine.answer("I have chapati, what sabji can I eat?", None, profile(["gluten"]))
    assert not r.fallback
    assert "I left out chapati because of your gluten allergy." in r.answer
    assert "Fill-up sabjis" in r.answer and "closer to the starchy part" in r.answer
    assert "chapati or rice" not in r.answer


def test_a_person_without_that_allergy_still_gets_the_approved_wording(engine):
    r = engine.answer("what sabji goes with chapati?", None, profile([]))
    assert "chapati or rice part of the meal" in r.answer and "left out" not in r.answer


# ---------------------------------------------------------------- the answer says what it was checked against
def test_the_note_lists_allergies_conditions_and_diet(engine):
    r = engine.answer("what sabji can I eat?", None, profile(["milk", "egg"], ["hypertension", "obesity"], diet_type="jain"))
    note = [line for line in r.answer.splitlines() if line.startswith("Kept in mind for you")]
    assert len(note) == 1
    for word in ("allergy to milk and eggs", "high blood pressure", "your weight goal", "Jain diet"):
        assert word in note[0], note[0]


@pytest.mark.parametrize("q", ["what sabji can I eat?", "I have lauki and tomato, what can I cook?", "can I have curd?", "give me recipes"])
def test_the_note_appears_on_food_answers_when_something_is_ticked(engine, q):
    assert "Kept in mind for you" in engine.answer(q, None, profile(["milk"], ["obesity"])).answer


def test_no_note_when_nothing_is_known(engine):
    assert "Kept in mind" not in engine.answer("what sabji can I eat?", None, profile([])).answer
    assert "Kept in mind" not in engine.answer("what sabji can I eat?", None, None).answer


@pytest.mark.parametrize("q", ["I have chest pain", "hello", "Which phone should I buy?", "how many carbs in one chapati?"])
def test_no_note_where_it_does_not_belong(engine, q):
    assert "Kept in mind" not in engine.answer(q, None, profile(["milk"], ["obesity"])).answer


def test_the_note_is_one_line_even_for_every_allergy(engine):
    r = engine.answer("what sabji can I eat?", None, profile(ALL, ["obesity", "hypertension", "diabetes"], diet_type="vegetarian"))
    notes = [line for line in r.answer.splitlines() if "Kept in mind" in line]
    assert len(notes) == 1 and len(notes[0]) < 300
    assert not r.fallback


@pytest.mark.parametrize("junk", [["<b>x</b>"], [5], [None], ["milk", "milk"], ["MILK"], ["  milk "], [""], "milk", 7])
def test_odd_profile_values_never_break_the_note(engine, junk):
    r = engine.answer("what sabji can I eat?", None, {"allergies": junk, "conditions": junk})
    assert_valid(r)
    assert "<b>" not in r.answer


# ---------------------------------------------------------------- the ticked conditions change what is offered
def test_ticked_conditions_alone_bring_dishes_that_pass_the_checks(engine):
    plain = engine.answer("what sabji can I eat?", None, profile([]))
    ticked = engine.answer("what sabji can I eat?", None, profile([], ["obesity"]))
    assert "Here are some light dishes" in ticked.answer and "Here are some light dishes" not in plain.answer


def test_a_salty_recipe_is_not_offered_to_someone_with_high_blood_pressure(engine):
    person = Person(allergies=[], conditions=["hypertension"])
    limits = engine.recipes.compile_limits(person)
    assert "sodium_mg" in limits and limits["sodium_mg"].verifiable
    assert "sodium_mg" not in engine.recipes.compile_limits(Person(allergies=[], conditions=[]))


def test_conditions_stay_for_the_rest_of_the_chat(engine):
    first = engine.answer("what sabji can I eat?", None, profile([], ["hypertension"]))
    second = engine.answer("give me recipes", first.conversation_id, profile([], ["hypertension"]))
    assert "Kept in mind for you: high blood pressure" in second.answer


def test_every_allergy_is_still_respected_in_a_picked_dish(engine):
    first = engine.answer("what sabji can I eat?", None, profile(["milk", "egg", "peanut"], ["obesity"]))
    for dish in [line[2:] for line in first.answer.splitlines() if line.startswith("- ") and "Sabji" in line or "Dal" in line]:
        r = engine.answer(dish, first.conversation_id, profile(["milk", "egg", "peanut"], ["obesity"]))
        for word in ("paneer", "ghee", "curd", "egg", "peanut"):
            assert f"- {word}" not in r.answer.lower(), (dish, word)
