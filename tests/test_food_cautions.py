"""Food cautions for a condition, a medicine or a pregnancy (cautions.py + data/food_cautions_test.json), and the profile values
that feed them (person.py)."""
import copy

import pytest

from dixit_answer.consistency import find_problems
from dixit_answer.main_pipeline import MainPipeline
from dixit_answer.person import sanitize_profile

KIDNEY = {"allergies": [], "diet_type": "vegetarian", "conditions": ["chronic kidney disease"]}
WARFARIN = {"allergies": [], "diet_type": "vegetarian", "medicines": ["Warfarin 5 mg"]}
PREGNANT = {"allergies": [], "diet_type": "vegetarian", "pregnant": True}
PLAIN = {"allergies": [], "diet_type": "vegetarian"}


@pytest.fixture(scope="module")
def main(engine):
    return MainPipeline(engine)


def ask(main, message, profile):
    return main.answer(message, None, profile)


@pytest.mark.parametrize("message, profile, line", [
    ("can I eat banana", KIDNEY, "With kidney disease, foods high in potassium, such as banana, may need to be limited."),
    ("can I eat palak", WARFARIN, "Because you take a blood thinner, keep the amount of green leafy vegetables such as palak about the same"),
    ("can I eat raw papaya", PREGNANT, "During pregnancy, raw or half-ripe papaya is usually advised against."),
    ("what can I make with aloo and onion", KIDNEY, "such as aloo, may need to be limited"),
])
def test_a_caution_is_added_when_the_person_and_the_food_match(main, message, profile, line):
    assert line in ask(main, message, profile).answer


@pytest.mark.parametrize("message, profile", [
    ("can I eat banana", PLAIN), ("can I eat banana", WARFARIN), ("can I eat palak", KIDNEY | {"conditions": []}),
    ("can I eat mango", PREGNANT),
])
def test_no_caution_without_both_the_person_and_the_food(main, message, profile):
    answer = ask(main, message, profile).answer
    assert "may need to be limited" not in answer and "blood thinner" not in answer and "During pregnancy" not in answer


def test_pregnancy_and_medicines_are_kept_in_mind(main):
    assert "Kept in mind for you: pregnancy and vegetarian diet." in ask(main, "can I eat mango", PREGNANT).answer
    assert "your medicines (Warfarin 5 mg)" in ask(main, "can I eat palak", WARFARIN).answer


def test_a_vegan_profile_leaves_out_milk_and_egg():
    assert sanitize_profile({"diet_type": "vegan", "allergies": ["peanut"]}) == {"diet_type": "vegetarian", "allergies": ["egg", "milk", "peanut"]}


def test_a_vegan_profile_is_not_told_curd_is_allowed(main):
    answer = ask(main, "can I eat curd", {"allergies": [], "diet_type": "vegan"}).answer
    assert "Curd won't suit you" in answer and "allowed" not in answer


def test_caution_problems_are_caught_at_start_up(engine):
    data = copy.deepcopy(engine.data)
    data.food_cautions["cautions"][0]["foods"].append("FOOD-NOPE")
    data.food_cautions["cautions"][1]["when"] = {"condition": "gout"}
    problems = "\n".join(find_problems(data, engine.library, engine.index, "development"))
    assert "unknown food FOOD-NOPE" in problems and "unknown condition 'gout'" in problems
