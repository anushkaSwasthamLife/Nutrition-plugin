"""'What more can I cook with paneer other than paneer bhurji': the dish named after "other than / except" is left out, never offered back."""
import pytest

from dixit_answer.understand import EXCLUDE
from tests.edge_helpers import assert_valid

PROFILE = {"allergies": ["soy"], "diet_type": "vegetarian"}
BHURJI = "Palak Paneer Bhurji"


def ask(engine, text, profile=PROFILE):
    return engine.answer(text, None, profile)


@pytest.mark.parametrize("text", [
    "what more can i cook if i have paneer other than paneer bhurji",
    "i have paneer, anything except paneer bhurji?",
    "I have paneer. Something other than palak paneer bhurji please",
    "i have paneer apart from palak paneer bhurji",
    "i have paneer, besides palak paneer bhurji",
    "i have paneer excluding palak paneer bhurji",
    "i have paneer but not palak paneer bhurji",
    "I HAVE PANEER OTHER THAN PALAK PANEER BHURJI!!!",
])
def test_the_excluded_dish_is_not_offered_and_nothing_is_passed_to_the_dietitian(full_engine, text):
    r = ask(full_engine, text)
    assert_valid(r, text)
    assert BHURJI not in r.answer, text
    assert "isn't in our food list" not in r.answer, r.answer[:200]
    assert "Paneer" in r.answer or "paneer" in r.answer


def test_without_the_exclusion_the_dish_is_offered(full_engine):
    assert BHURJI in ask(full_engine, "what can i cook if i have paneer").answer


def test_the_other_dishes_with_paneer_are_still_offered(full_engine):
    assert "Capsicum Paneer Sabji" in ask(full_engine, "what more can i cook if i have paneer other than paneer bhurji").answer


def test_a_food_named_after_the_exclusion_is_not_a_pantry_item(full_engine):
    r = ask(full_engine, "i have aloo, apart from jeera aloo what else")
    assert_valid(r)
    assert "Jeera Aloo" not in r.answer and "isn't in our food list" not in r.answer


def test_the_allergy_rules_still_apply_to_what_is_left(full_engine):
    r = ask(full_engine, "i have paneer other than paneer bhurji", {"allergies": ["milk"]})
    assert_valid(r)
    assert "Capsicum Paneer Sabji" not in r.answer and BHURJI not in r.answer


def test_an_exclusion_with_no_dish_after_it_does_not_crash(full_engine):
    for text in ("i have paneer other than", "i have paneer except", "other than", "except for", "i have paneer other than xyzzy"):
        assert_valid(ask(full_engine, text), text)


@pytest.mark.parametrize("text, expected", [("what can i cook other than dal", "dal"), ("i have aloo apart from jeera aloo", "jeera aloo"),
                                            ("a b c except for x y", "x y"), ("nothing here", None), ("another than that", None), ("exceptional dal", None)])
def test_the_exclusion_pattern(text, expected):
    m = EXCLUDE.search(text)
    assert (m.group("x") if m else None) == expected
