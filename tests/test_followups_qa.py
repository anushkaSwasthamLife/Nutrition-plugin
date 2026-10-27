"""Follow-ups found in the live question run: picking a dish by number, "I don't eat onion" with an apostrophe, "how many calories
in it" after a recipe, and "yes, it's about food" with no food named."""
import pytest

from dixit_answer.main_pipeline import MainPipeline
from dixit_answer.understand import choice_number

VEG = {"allergies": [], "diet_type": "vegetarian"}


@pytest.fixture(scope="module")
def main(full_engine):
    return MainPipeline(full_engine)


def chat(main, *messages, profile=VEG):
    out, cid = [], None
    for m in messages:
        r = main.answer(m, cid, profile)
        cid = r.conversation_id
        out.append(r)
    return out


@pytest.mark.parametrize("text, count, n", [
    ("2", 6, 2), ("the third one", 6, 3), ("last one", 6, 6), ("dusra wala", 3, 2), ("number 1 please", 3, 1),
    ("9", 6, 0), ("2 rotis", 6, 0), ("0", 6, 0), ("what is the second step", 6, 0),
])
def test_choice_number(text, count, n):
    assert choice_number(text, count) == n


def test_a_dish_is_picked_from_the_list_by_number(main):
    listed, picked = chat(main, "what can I make with aloo and onion", "2")
    assert "- Jeera Aloo" in listed.answer and picked.type == "recipe_request" and "**Jeera Aloo**" in picked.answer


@pytest.mark.parametrize("statement", ["I don't eat onion", "I dont eat onion", "I do not eat onion"])
def test_i_dont_eat_is_remembered_with_or_without_the_apostrophe(main, statement):
    said, listed = chat(main, statement, "what can I make with aloo and onion")
    assert said.type == "profile_statement" and "I left out onion" in listed.answer


@pytest.mark.parametrize("statement", ["I'm vegan", "I am vegan"])
def test_im_vegan_with_the_apostrophe(main, statement):
    said, curd = chat(main, statement, "can I eat curd")
    assert said.type == "profile_statement" and "allowed" not in curd.answer


def test_calories_in_it_after_a_recipe_uses_the_recipe(main):
    _recipe, it, protein = chat(main, "what can I make with aloo and onion", "2", "how many calories in it")[1:] + chat(main, "calories in jeera aloo")
    assert it.answer.startswith("For our Jeera Aloo recipe (1 dish for 2 people): about 330 kcal")
    assert protein.answer == it.answer


def test_and_protein_after_a_recipe_stays_on_the_recipe(main):
    _r, _cal, protein = chat(main, "what can I make with aloo and onion", "2", "how many calories in it")[0:1] + \
        chat(main, "calories in jeera aloo", "and protein?")
    assert protein.answer.startswith("For our Jeera Aloo recipe")


def test_a_named_recipe_is_not_split_into_its_foods(main):
    answer = chat(main, "calories in jeera aloo")[0].answer
    assert "cumin" not in answer and answer.startswith("For our Jeera Aloo recipe")


def test_yes_its_about_food_without_a_food_asks_which_food(main):
    _q, yes, dal = chat(main, "is it good for my health", "yes", "dal")
    assert yes.answer.startswith("Which food or dish do you mean?") and dal.handled_by == "plugin"
