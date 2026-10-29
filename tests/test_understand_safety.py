import pytest

from dixit_answer import safety
from dixit_answer.state import ChatState
from dixit_answer.understand import understand

CASES = [
    ("i have obesity . so what should i eat to reduce it", "food_choice_guidance"),
    ("i have chapati but what sabji can i eat with it", "food_choice_guidance"),
    ("give me recipes", "recipe_request"),
    ("how do I make bhindi sabzi?", "recipe_request"),
    ("i have aloo and onion so what indian meal i can prepare", "meal_from_ingredients"),
    ("How many carbs in one idli?", "neutral_nutrient_lookup"),
    ("compare dosa and idli and rice", "neutral_food_comparison"),
    ("Can I have mango?", "food_allowed"),
    ("aloo", "food_allowed"),
    ("Is rice safe for me?", "personal_suitability"),
    ("Can I snack between meals?", "meal_protocol_application"),
    ("I'm now non diabetic, can I eat fruit?", "status_dependent_protocol_question"),
    ("How does the program work?", "protocol_explain"),
    ("I feel very hungry between meals", "support_struggle"),
    ("I feel hungry all the time", "support_struggle"),
    ("Which phone should I buy?", "out_of_scope"),
    ("thank you", "greeting_thanks"),
    ("where does this number come from?", "source_fact_lookup"),
]


@pytest.mark.parametrize("message,expected", CASES)
def test_question_types(engine, message, expected):
    u = understand(message, ChatState("t"), engine.index, engine.words)
    assert u.type == expected


def test_short_answer_to_our_question_is_a_reply(engine):
    state = ChatState("t", turn=2, last_followup={"id": "FU-HOME-FOODS", "slot": "home_foods", "turn": 2})
    u = understand("bhindi, aloo, cauliflower", state, engine.index, engine.words)
    assert u.type == "followup_reply"
    assert u.slot_fill["home_foods"] == ["FOOD-BHINDI", "FOOD-ALOO", "FOOD-CAULIFLOWER"]


def test_reply_with_a_new_question_keeps_the_question(engine):
    state = ChatState("t", turn=1, last_followup={"id": "FU-SAFETY", "slot": "safety", "turn": 1})
    u = understand("No medicines. I have chapati, what sabji can I eat?", state, engine.index, engine.words)
    assert u.is_reply and u.type == "food_choice_guidance" and u.pairs_with == ["FOOD-CHAPATI"]


def test_emergency_and_risk_words(engine):
    words = engine.words
    assert safety.check("I am shaking and sweating, I took insulin", ChatState("t"), words).emergency
    assert safety.check("I take insulin twice a day", ChatState("t"), words).updates["diabetes_medicine"] == "yes"
    assert safety.check("I don't take insulin", ChatState("t"), words).updates["diabetes_medicine"] == "no"
    assert safety.check("I am 15 years old", ChatState("t"), words).updates["under_18"] == "yes"
    assert not safety.check("I have hypothyroidism", ChatState("t"), words).emergency


@pytest.mark.parametrize("message, expected", [
    ("what should my 5 year old eat", None),                         # live run 27 Oct 2026: the parent was marked under 18
    ("my son is 8 years old, what can he eat", None),
    ("meri beti 12 years old hai, kya khilau", None),
    ("my daughter is 16 years old and I am 45, can I eat dal", "no"),  # the person's own age still counts
    ("I am 15 years old and my mother is 40 years old", "yes"),
    ("my mom says I am 15, can I eat paneer", "yes"),               # PR #4 review: "I am N" is always the person's own
    ("my friend told me I am 14", "yes"),
    ("my mom says i'm 16", "yes"),
    ("my age is 15 years old", "yes"),
    ("my son aged 8 eats dal", None),
])
def test_someone_elses_age_never_changes_the_persons_own_status(engine, message, expected):
    assert safety.check(message, ChatState("t"), engine.words).updates.get("under_18") == expected
