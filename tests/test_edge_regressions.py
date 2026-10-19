"""One test per real bug the edge testing found (so none can come back)."""
from edge_helpers import assert_valid


def _ask(engine, message, profile=None):
    r = engine.answer(message, None, profile)
    assert_valid(r, message)
    return r


def test_sprouts_question_never_offers_eggs_to_a_vegetarian(engine):
    r = _ask(engine, "I have aloo and onion, what can I make?", {"allergies": ["milk"], "diet_type": "vegetarian"})
    assert "egg" not in r.answer.lower()


def test_diet_question_not_asked_when_the_app_already_knows_it(engine):
    r = _ask(engine, "can I eat sweets?", {"allergies": [], "diet_type": "vegetarian"})
    assert "Do you eat vegetarian" not in r.answer


def test_diet_question_drops_eggs_for_an_egg_allergy(engine):
    r = _ask(engine, "can I eat moong dal & cabbage", {"allergies": ["egg"]})
    assert "with eggs" not in r.answer


def test_protocol_amounts_leave_out_blocked_options(engine):
    egg = _ask(engine, "how does the program work?", {"allergies": ["egg"]})
    assert "boiled eggs" not in egg.answer and "sprouted moong" in egg.answer and not egg.fallback
    nuts = _ask(engine, "how does the program work?", {"allergies": ["tree_nut", "peanut"]})
    assert "almond" not in nuts.answer.lower() and "ask your Dixit coach" in nuts.answer and not nuts.fallback
    chick = _ask(engine, "how does the program work?", {"allergies": ["chickpea"]})
    assert "chole" not in chick.answer and "boiled eggs" in chick.answer


def test_protocol_amounts_unchanged_when_nothing_is_blocked(engine):
    r = _ask(engine, "how does the program work?", {"allergies": []})
    assert "sprouted moong, matki or chole, or 2 boiled eggs, in the same quantity as the salad" in r.answer


def test_nutrition_lookup_of_a_blocked_food_says_why_instead_of_falling_back(engine):
    r = _ask(engine, "carbs in paneer, dal, tomato what can I make?", {"allergies": ["milk"]})
    assert not r.fallback and "won't suit you" in r.answer and "g of carbohydrate" in r.answer


def test_tips_never_name_a_food_the_person_cannot_have(engine):
    r = _ask(engine, "how do I make paneer and peanuts and kachalu every three ours?", {"allergies": [], "diet_type": "jain"})
    assert not r.fallback and "aloo" not in r.answer.lower()


def test_close_match_confirm_never_suggests_a_blocked_food(engine):
    r = _ask(engine, "I've got eegg, okra?", {"allergies": ["gluten"], "diet_type": "vegetarian"})
    assert "Did you mean eggs" not in r.answer


def test_allergy_statement_is_not_treated_as_a_pantry_word(engine):
    r = _ask(engine, "I have palak & dahi & potato & curd I am allergic tto milk", {"allergies": ["milk"]})
    assert not r.fallback and "allergic" not in r.answer
    assert "isn't in our food list" not in r.answer


def test_unused_pantry_food_is_said_not_dropped(engine):
    r = _ask(engine, "I have palak and potato", {"allergies": []})
    if "Palak Sabji" in r.answer and "aloo" not in r.answer.lower().split("**how:**")[0]:
        assert "doesn't use your aloo" in r.answer


def test_hinglish_foods_plus_what_to_make_is_a_recipe_request(engine):
    r = _ask(engine, "aloo pyaz hai kya banau", {"allergies": []})
    assert r.plan["type"] == "meal_from_ingredients" and ("You need" in r.answer or "Which one should I show?" in r.answer)
