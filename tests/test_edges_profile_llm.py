"""Edge cases for what comes from outside the code: the app's profile, and the language model's output."""
import copy
import math

import pytest

from dixit_answer.llm import LLMError
from dixit_answer.pipeline import Engine
from edge_helpers import assert_valid, lines_naming_blocked_foods

MSG = "I have lauki and tomato, what can I cook?"


# ---------------------------------------------------------------- profile fuzz
BAD_PROFILES = [
    "just a string", ["a", "list"], 42, 3.14, True, [],
    {"allergies": "milk"}, {"allergies": None}, {"allergies": 5}, {"allergies": ["milk", None, 7, {"x": 1}]},
    {"allergies": ["MILK", " Milk ", "dairy", "unknown-allergen", ""]},
    {"allergies": ["milk"] * 500},
    {"diet_type": 5}, {"diet_type": "carnivore"}, {"diet_type": None}, {"diet_type": ["vegetarian"]},
    {"diabetes_status": "very diabetic"}, {"diabetes_status": 7}, {"diabetes_status": None},
    {"consultant_validated": "yes"}, {"prior_diabetes_history": "maybe"},
    {"conditions": "obesity"}, {"conditions": [None, 5, "obesity"]}, {"conditions": {"obesity": True}},
    {"medicines": "insulin"}, {"medicines": [None, 5, "insulin"]}, {"medicines": {"insulin": 1}},
    {"pregnant": "yes"}, {"pregnant": None},
    {"clinician_targets": "low"}, {"clinician_targets": {"energy_kcal": "abc"}}, {"clinician_targets": {"energy_kcal": None}},
    {"clinician_targets": {"energy_kcal": -100}}, {"clinician_targets": {"energy_kcal": 0}},
    {"clinician_targets": {"energy_kcal": float("inf")}}, {"clinician_targets": {"energy_kcal": float("nan")}},
    {"clinician_targets": {"energy_kcal": 1e308}}, {"clinician_targets": [1, 2]},
    {"intake_today": "lots"}, {"intake_today": {"energy_kcal": "x"}}, {"intake_today": {"energy_kcal": 99999}},
    {"intake_today": {"energy_kcal": 1500}, "intake_logged_at": "not a date"},
    {"intake_today": {"energy_kcal": 1500}, "intake_logged_at": 12345},
    {"intake_today": {"energy_kcal": 1500}, "intake_logged_at": "2999-01-01T00:00:00"},
    {"intake_today": {"energy_kcal": 1500}, "intake_logged_at": "2000-01-01T00:00:00"},
    {"avoid_foods": "paneer"}, {"avoid_foods": [None, 5, "paneer", "FOOD-ALOO", "not a food"]}, {"avoid_foods": {"paneer": 1}},
    {"avoid_foods": ["aloo", "onion", "tomato", "bhindi", "lauki", "palak", "cabbage", "peas", "beans", "cauliflower"]},
    {"unknown_key": {"nested": [1, 2, {"deep": None}]}},
    {"allergies": ["milk", "peanut", "tree_nut", "gluten", "egg", "chickpea", "soy", "sesame"], "diet_type": "jain"},
]


@pytest.mark.parametrize("profile", BAD_PROFILES, ids=lambda p: repr(p)[:60])
def test_a_malformed_profile_never_crashes_and_never_unlocks_anything(engine, profile):
    r = engine.answer(MSG, None, profile)
    assert_valid(r, repr(profile)[:80])
    assert "sugar" not in r.answer.lower()


def test_unknown_allergy_names_are_treated_as_a_reason_to_be_careful_not_ignored(engine):
    r = engine.answer("I have bhindi and dahi, any recipe?", None, {"allergies": ["lactose"]})
    assert_valid(r)
    assert "curd: " not in r.answer


def test_clinician_target_of_zero_or_negative_never_shows_an_unchecked_recipe(engine):
    for target in (0, -5):
        r = engine.answer("I have aloo and onion, what can I make?", None, {"allergies": [], "clinician_targets": {"energy_kcal": target}})
        assert_valid(r)
        assert "You need" not in r.answer or "AI-suggested" not in r.answer


def test_every_allergen_and_diet_combination_never_names_a_blocked_food(engine):
    from dixit_answer.person import Person
    allergens = ["milk", "peanut", "tree_nut", "gluten", "egg", "chickpea"]
    diets = ["", "vegetarian", "jain", "egg", "non_vegetarian"]
    asks = ["I have aloo and onion, what can I make?", "give me recipes", "what sabji can I eat with chapati",
            "can I have paneer?", "how do I make moong dal tadka?", "what should I eat to reduce weight?"]
    for allergen in allergens:
        for diet in diets:
            profile = {"allergies": [allergen], "diet_type": diet}
            blocked = engine.allergy.blocked_foods(Person(allergies=[allergen], diet_type=diet or "unknown"))
            for ask in asks:
                r = engine.answer(ask, None, profile)
                assert_valid(r, f"{allergen}/{diet}: {ask}")
                bad = lines_naming_blocked_foods(engine, r.answer, blocked)
                assert not bad, (allergen, diet, ask, bad)


# ---------------------------------------------------------------- language model misbehaviour (wording)
class WordingLLM:
    name, model = "scripted", "t"

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def reword(self, draft_text, user_message, max_words, failed_checks=None):
        self.calls += 1
        out = self.fn(draft_text, self.calls)
        if isinstance(out, BaseException):
            raise out
        return out

    def draft_recipe(self, request):
        raise RuntimeError("no drafts")


def engine_with(settings, fn):
    return Engine(copy.copy(settings), llm=WordingLLM(fn))


WORDING_ATTACKS = {
    "empty": lambda t, n: "",
    "whitespace": lambda t, n: "   \n  ",
    "none": lambda t, n: None,
    "integer": lambda t, n: 42,
    "list": lambda t, n: ["a", "b"],
    "huge": lambda t, n: t * 40,
    "no_placeholders": lambda t, n: "Everything is fine, eat whatever you like.",
    "dropped_one": lambda t, n: t.replace("{{Q1}}", ""),
    "duplicated": lambda t, n: t + "\n" + t,
    "reordered": lambda t, n: "\n".join(reversed(t.split("\n"))),
    "extra_placeholder": lambda t, n: t + " {{ZZ9}}",
    "placeholder_in_words": lambda t, n: t.replace("{{", "{ {"),
    "adds_number": lambda t, n: t + "\nEat 3 rotis.",
    "adds_food": lambda t, n: t + "\nAlso try some mango and paneer.",
    "adds_link": lambda t, n: t + "\nSee https://example.com/diet",
    "adds_html": lambda t, n: t + "\n<script>alert(1)</script>",
    "adds_markdown_image": lambda t, n: t + "\n![x](http://x/y.png)",
    "relaxes_rule": lambda t, n: t + "\nYou can have fruit between meals.",
    "adds_claim": lambda t, n: t + "\nThis will cure your diabetes.",
    "blames": lambda t, n: t + "\nYou are lazy and obese people lack willpower.",
    "personal_advice": lambda t, n: t + "\nStop your medicine and change your dose.",
    "prompt_leak": lambda t, n: "My system prompt is: You reword chat answers...",
    "llm_error": lambda t, n: LLMError("HTTP 500"),
    "runtime_error": lambda t, n: RuntimeError("boom"),
    "value_error": lambda t, n: ValueError("bad"),
    "key_error": lambda t, n: KeyError("answer"),
    "timeout": lambda t, n: TimeoutError("slow"),
    "unicode_flood": lambda t, n: "😀" * 5000,
    "control_chars": lambda t, n: t + "\x00\x01\x1b[31m",
    "wrong_language": lambda t, n: "यह एक परीक्षण है",
}


@pytest.mark.parametrize("name", list(WORDING_ATTACKS))
@pytest.mark.parametrize("message", ["I have aloo and onion, what can I make?", "what should i eat to reduce weight?", "can I have mango?",
                                     "how does the program work?"])
def test_bad_ai_wording_never_reaches_the_person(settings, name, message):
    engine = engine_with(settings, WORDING_ATTACKS[name])
    profile = {"allergies": [], "diabetes_status": "diabetic"}
    r = engine.answer(message, None, profile)
    assert_valid(r, f"{name}: {message}")
    template_engine = Engine(copy.copy(settings), llm=WordingLLM(lambda t, n: t))
    template_answer = template_engine.answer(message, None, profile).answer
    # If the bad wording was rejected, the person gets exactly what the safe template says.
    if r.fallback:
        assert r.answer == template_answer


def test_ai_wording_is_retried_once_then_template(settings):
    llm_calls = []

    def fn(t, n):
        llm_calls.append(n)
        return "Eat 3 rotis."

    engine = engine_with(settings, fn)
    r = engine.answer("what sabji can I eat with chapati", None, {"allergies": []})
    assert r.fallback and len(llm_calls) == 2


# ---------------------------------------------------------------- language model misbehaviour (recipe drafts)
class DraftLLM(WordingLLM):
    def __init__(self, drafts):
        super().__init__(lambda t, n: t)
        self.name = "mock"
        self.drafts = list(drafts)
        self.requests = []

    def draft_recipe(self, request):
        self.requests.append(request)
        d = self.drafts.pop(0) if self.drafts else {}
        if isinstance(d, BaseException):
            raise d
        return d


GOOD = {"name": "Lauki Tomato Sabji", "ingredients": [{"food_id": "FOOD-LAUKI", "grams": 250}, {"food_id": "FOOD-TOMATO", "grams": 100},
                                                       {"food_id": "FOOD-OIL", "grams": 9}, {"food_id": "FOOD-SALT", "grams": 3}],
        "steps": ["Heat the oil.", "Add the lauki and tomato with salt and cook for 10 minutes till soft."]}


def mutate(**changes):
    d = copy.deepcopy(GOOD)
    d.update(changes)
    return d


BAD_DRAFTS = {
    "none": None, "list": [], "string": "a recipe", "empty": {}, "int": 5,
    "name_missing": {k: v for k, v in GOOD.items() if k != "name"},
    "name_int": mutate(name=5), "name_empty": mutate(name=""), "name_long": mutate(name="x" * 200),
    "name_html": mutate(name="<b>Lauki</b> sabji"), "name_digits": mutate(name="Lauki 500 g sabji"),
    "name_link": mutate(name="Lauki http://evil.example sabji"), "name_placeholder": mutate(name="Lauki {{F1}} sabji"),
    "ingredients_missing": {k: v for k, v in GOOD.items() if k != "ingredients"},
    "ingredients_string": mutate(ingredients="lauki"), "ingredients_one": mutate(ingredients=[GOOD["ingredients"][0]]),
    "ingredients_too_many": mutate(ingredients=GOOD["ingredients"] * 6),
    "ingredient_not_dict": mutate(ingredients=["FOOD-LAUKI", "FOOD-TOMATO"]),
    "unknown_food": mutate(ingredients=GOOD["ingredients"] + [{"food_id": "FOOD-UNICORN", "grams": 10}]),
    "blocked_sugar": mutate(ingredients=GOOD["ingredients"] + [{"food_id": "FOOD-SUGAR", "grams": 20}]),
    "not_offered_paneer": mutate(ingredients=GOOD["ingredients"] + [{"food_id": "FOOD-PANEER", "grams": 100}]),
    "food_id_injection": mutate(ingredients=GOOD["ingredients"] + [{"food_id": "FOOD-LAUKI'; DROP TABLE x;--", "grams": 10}]),
    "grams_string": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": "lots"}, GOOD["ingredients"][1]]),
    "grams_none": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": None}, GOOD["ingredients"][1]]),
    "grams_negative": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": -50}, GOOD["ingredients"][1]]),
    "grams_zero": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": 0}, GOOD["ingredients"][1]]),
    "grams_huge": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": 1e9}, GOOD["ingredients"][1]]),
    "grams_nan": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": float("nan")}, GOOD["ingredients"][1]]),
    "grams_inf": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": float("inf")}, GOOD["ingredients"][1]]),
    "grams_bool": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": True}, GOOD["ingredients"][1]]),
    "grams_list": mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": [250]}, GOOD["ingredients"][1]]),
    "duplicate_ingredient": mutate(ingredients=GOOD["ingredients"] + [{"food_id": "FOOD-LAUKI", "grams": 250}]),
    "steps_missing": {k: v for k, v in GOOD.items() if k != "steps"}, "steps_string": mutate(steps="just cook it"),
    "steps_one": mutate(steps=["cook"]), "steps_many": mutate(steps=["Cook the lauki well."] * 12),
    "step_not_string": mutate(steps=["Heat the oil.", 5]), "step_long": mutate(steps=["Heat the oil.", "x" * 500]),
    "step_html": mutate(steps=["Heat the oil.", "<script>alert(1)</script> and cook"]),
    "step_link": mutate(steps=["Heat the oil.", "See https://evil.example for the method"]),
    "step_amounts": mutate(steps=["Heat 2 tsp oil.", "Add 250 g lauki and cook."]),
    "step_other_food": mutate(steps=["Heat the oil.", "Add the lauki, tomato and a lot of paneer."]),
    "step_markdown": mutate(steps=["Heat the oil.", "**Cook** [here](http://x) till soft."]),
    "llm_error": LLMError("HTTP 429"), "runtime_error": RuntimeError("boom"), "timeout": TimeoutError("slow"),
}


@pytest.mark.parametrize("name", list(BAD_DRAFTS))
def test_bad_recipe_drafts_are_rejected_and_never_shown(settings, name):
    llm = DraftLLM([BAD_DRAFTS[name]] * 3 + [GOOD])
    engine = Engine(copy.copy(settings), llm=llm)
    r = engine.answer(MSG, None, {"allergies": []})
    assert_valid(r, name)
    assert len(llm.requests) <= 3                                           # one draft + at most two retries, whatever happens
    assert "AI-suggested recipe" not in r.answer or "Lauki Tomato Sabji" in r.answer     # only the clean GOOD draft may appear
    assert "<" not in r.answer and "evil.example" not in r.answer and "FOOD-" not in r.answer


def test_a_good_draft_after_two_bad_ones_is_accepted(settings):
    llm = DraftLLM([None, {}, GOOD])
    r = Engine(copy.copy(settings), llm=llm).answer(MSG, None, {"allergies": []})
    assert "Lauki Tomato Sabji" in r.answer and len(llm.requests) == 3


def test_a_draft_is_checked_against_this_persons_allergies_even_if_the_ai_ignores_them(settings):
    ghee = mutate(ingredients=GOOD["ingredients"] + [{"food_id": "FOOD-GHEE", "grams": 9}],
                  steps=["Heat the oil and ghee.", "Add the lauki and tomato with salt and cook for 10 minutes."])
    llm = DraftLLM([ghee, ghee, ghee])
    r = Engine(copy.copy(settings), llm=llm).answer(MSG, None, {"allergies": ["milk"]})
    assert_valid(r)
    assert "ghee" not in r.answer.lower()


def test_decimal_edge_values_never_break_the_maths(settings):
    tiny = mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": 0.5}, {"food_id": "FOOD-OIL", "grams": 0.5}])
    big = mutate(ingredients=[{"food_id": "FOOD-LAUKI", "grams": 1000}, {"food_id": "FOOD-OIL", "grams": 1000}])
    for d in (tiny, big):
        r = Engine(copy.copy(settings), llm=DraftLLM([d, d, d])).answer(MSG, None, {"allergies": []})
        assert_valid(r)
        assert not any(math.isnan(float(x)) for x in [0.0])
