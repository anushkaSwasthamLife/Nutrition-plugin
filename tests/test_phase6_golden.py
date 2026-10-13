"""Phase 6: Golden Rules engine, diabetes status, fruit position, consultant validation, rule registry."""
import copy
import json

import pytest

from dixit_answer.golden import GoldenRules, Status
from dixit_answer.loader import LoadError, load_swastham_data
from dixit_answer.pipeline import Engine
from dixit_answer.state import ChatState
from dixit_answer.status import apply_status, parse_status
from dixit_answer.understand import understand


def failed(result):
    return [c for c in result.checks if not c["passed"]]


@pytest.fixture
def ask(engine):
    def run(message, profile=None, cid=None):
        return engine.answer(message, cid, profile if profile is not None else {"allergies": []})
    return run


# ---------------------------------------------------------------- status parsing
@pytest.mark.parametrize("text,expected", [
    ("I am diabetic", {"status": "diabetic"}),
    ("I have type 2 diabetes", {"status": "diabetic"}),
    ("my sugar is high, I'm a sugar patient", {"status": "diabetic"}),
    ("I have prediabetes", {"status": "prediabetic"}),
    ("I'm pre-diabetic", {"status": "prediabetic"}),
    ("I do not have diabetes", {"status": "non_diabetic", "prior_history": False}),
    ("I'm not diabetic", {"status": "non_diabetic", "prior_history": False}),
    ("My diabetes is now normal", {"status": "currently_non_diabetic", "prior_history": True}),
    ("I used to be diabetic", {"status": "currently_non_diabetic", "prior_history": True}),
    ("I reversed my diabetes", {"status": "currently_non_diabetic", "prior_history": True}),
    ("my consultant has confirmed it", {"consultant_validated": True}),
    ("it has not yet been confirmed by my consultant", {"consultant_validated": False}),
])
def test_status_is_read_from_what_the_person_says(text, expected):
    assert parse_status(text) == expected


@pytest.mark.parametrize("text", [
    "Can someone with diabetes eat mango?",          # about someone else
    "diabetic friendly recipes please",              # not a statement about the speaker
    "I want healthy recipes",                        # 'healthy' must not mean 'not diabetic'
    "what foods are bad for diabetes",
    "",
])
def test_third_person_and_generic_mentions_do_not_set_a_status(text):
    assert parse_status(text) == {}


def test_a_new_status_clears_an_old_validation():
    state = ChatState("x")
    apply_status(state, {"status": "currently_non_diabetic", "prior_history": True, "consultant_validated": True})
    apply_status(state, {"status": "diabetic"})
    assert state.consultant_validated is None


# ---------------------------------------------------------------- the engine (data driven, nothing guessed)
def test_fruit_position_by_status(engine):
    g = engine.services.golden
    assert g.fruit_position(Status("non_diabetic", False)).allowed == "within_meal_window"
    unvalidated = g.fruit_position(Status("currently_non_diabetic", True, False))
    assert unvalidated.allowed == "no" and not unvalidated.needs_consultant
    assert g.fruit_position(Status("currently_non_diabetic", True, None)).needs_consultant        # restricted until told
    assert g.fruit_position(Status("diabetic")).allowed == "unknown"                              # rule not in our data: said, not guessed
    assert g.fruit_position(Status("prediabetic")).allowed == "unknown"
    assert g.fruit_position(Status("unknown")).needs_status
    assert g.fruit_position(Status("currently_non_diabetic", True, True)).allowed == "unknown"    # validated: base package needed


def test_unknown_rules_point_to_the_coach_instead_of_inventing(engine):
    text = engine.services.golden.fruit_position(Status("diabetic")).text
    assert "can't state it here yet" in text and "Dixit coach" in text
    assert "Dixit coach" in engine.services.golden.between_meals()


def test_golden_rules_file_is_marked_unsigned_and_production_refuses_it(settings):
    data = json.loads((__import__("pathlib").Path(__file__).parent.parent / "dixit_answer/data/golden_rules.json").read_text(encoding="utf-8"))
    assert data["status"] == "PARTIAL" and all(not r["confirmed_by_base_package"] for r in data["rules"])
    prod = copy.copy(settings)
    prod.environment = "production"
    with pytest.raises(LoadError, match="PARTIAL|TEST ONLY|STUB"):
        load_swastham_data(prod)


# ---------------------------------------------------------------- amendment tests NUT-T005 / T008 / T011 / T012 / T017
def test_T005_mango_for_a_diabetic_shares_facts_and_states_the_fruit_rule_separately(ask):
    r = ask("Is mango nutritious for someone with diabetes?", {"allergies": [], "diabetes_status": "diabetic"})
    assert "Mango" in r.answer and "g carbohydrate per 100 g" in r.answer or "kcal per 100 g" in r.answer   # facts shown
    assert "fruit rule" in r.answer and "not permission" in r.answer and not failed(r)
    assert "you can" not in r.answer.lower()


def test_T011_non_diabetic_fruit_between_meals_is_not_allowed(ask):
    r = ask("I am non-diabetic and want fruit between meals.")
    assert "only within the 55-minute meal window, never between meals" in r.answer and not failed(r)


def test_T012_now_normal_does_not_allow_fruit_until_the_consultant_validates(ask):
    r1 = ask("My diabetes is now normal. Can I eat fruit?")
    assert "restriction continues until your consultant confirms" in r1.answer
    assert "Has your consultant confirmed" in r1.answer and not failed(r1)
    r2 = ask("No, not yet", cid=r1.conversation_id)
    assert "restriction continues" in r2.answer
    r3 = ask("my consultant has confirmed it", cid=r1.conversation_id)
    assert "restriction continues" not in r3.answer and "can't state it here yet" in r3.answer     # no invented rule
    assert not failed(r3)


def test_T017_every_three_hours_gets_the_programme_position_not_frequent_meals(ask):
    r = ask("Isn't eating every three hours better for blood sugar?")
    assert "two meals a day" in r.answer.lower() and "don't have approved evidence" in r.answer
    assert "How Dixit meals work" in r.answer and not failed(r)


def test_status_is_asked_once_and_only_when_a_rule_depends_on_it(ask):
    r1 = ask("Can I have mango?")
    assert "fruit rule depends on your diabetes status" in r1.answer and "Do you have diabetes" in r1.answer
    r2 = ask("I have type 2 diabetes", cid=r1.conversation_id)
    assert "fruit rule" in r2.answer and "Do you have diabetes" not in r2.answer
    plain = ask("How many carbs in one idli?")
    assert "Do you have diabetes" not in plain.answer                      # plain facts never ask (N9)
    veg = ask("Can I have bhindi?")
    assert "Do you have diabetes" not in veg.answer                        # vegetables don't depend on status


def test_no_to_the_status_question_means_non_diabetic(ask):
    r1 = ask("Can I have a banana?")
    assert r1.type == "food_allowed"                                        # 'banana' is a fruit, not the Hindi word for 'make'
    r2 = ask("no", cid=r1.conversation_id)
    assert "never had diabetes" in r2.answer and "55-minute meal window" in r2.answer


def test_profile_status_beats_asking(ask):
    r = ask("can I have mango?", {"allergies": [], "diabetes_status": "non_diabetic"})
    assert "Do you have diabetes" not in r.answer and "meal window" in r.answer


# ---------------------------------------------------------------- recipes and the Golden Rules
def test_fruit_is_left_out_of_recipes_unless_confirmed_non_diabetic(ask):
    r = ask("I have mango, onion and aloo, what can I make?")
    assert "I left out mango because of the programme's fruit rule" in r.answer and not failed(r)
    assert "mango" not in r.answer.split("**You need**")[1].lower()


def test_recipe_gate_names_the_golden_rule(engine):
    from dixit_answer.person import Person
    from dixit_answer.recipes import CheckedRecipe
    svc = engine.recipes
    recipe = CheckedRecipe(recipe_id="T", origin="ai_suggested", name="Mango Sabji", makes="1 dish", kind="sabji",
                           ingredients=[{"food": "FOOD-MANGO", "grams": 100, "unit": ""}, {"food": "FOOD-ONION", "grams": 100, "unit": ""}],
                           steps=["Cook the mango and the onion."])
    svc.calculate(recipe)
    for status, passes in ((Status("diabetic"), False), (Status("unknown"), False), (Status("non_diabetic", False), True)):
        person = Person(allergies=[], status=status)
        gate = svc.gate(recipe, person, svc.compile_limits(person), set())
        assert gate.passed is passes, (status, gate.reasons)


# ---------------------------------------------------------------- wording can't relax a Golden Rule
class WordingLLM:
    name, model = "scripted", "t"

    def __init__(self, text):
        self.text = text

    def reword(self, draft_text, user_message, max_words, failed_checks=None):
        return self.text(draft_text) if callable(self.text) else self.text

    def draft_recipe(self, request):
        raise RuntimeError("no drafts")


@pytest.mark.parametrize("claim", ["You can eat fruit between meals.", "Fruit is allowed any time.", "Try three meals a day instead.",
                                   "Small frequent meals are fine.", "Snack on fruit if you are hungry."])
def test_ai_wording_that_relaxes_a_golden_rule_is_replaced_by_the_template(settings, claim):
    engine = Engine(copy.copy(settings), llm=WordingLLM(lambda t: t + "\n" + claim))
    r = engine.answer("Can I have mango?", None, {"allergies": [], "diabetes_status": "non_diabetic"})
    assert r.fallback and claim not in r.answer


# ---------------------------------------------------------------- understanding fixes found while testing
@pytest.mark.parametrize("message,expected", [
    ("can I have banana", "food_allowed"),
    ("kya bana sakta hu aloo pyaz se, I have aloo and pyaz", "meal_from_ingredients"),
    ("yes, my consultant confirmed it", "status_dependent_protocol_question"),
    ("Can I eat small frequent meals?", "meal_protocol_application"),
])
def test_classification_fixes(engine, message, expected):
    assert understand(message, ChatState("t"), engine.index, engine.words).type == expected


# ---------------------------------------------------------------- rule registry
def test_rules_registry_orders_swastham_rules_below_the_golden_rules(engine):
    reg = engine.data.rules_registry
    order = [r["id"] for r in reg["order"]]
    assert order.index("NUT-ALLERGEN-001") < order.index("NUT-GOLDEN-001") < order.index("NUT-SWAP-001") < order.index("NUT-RECIPE-001")
    for rule in reg["order"]:
        if rule["owner"] == "Swastham" and rule["id"] in {"NUT-SWAP-001", "NUT-RECIPE-001", "NUT-LIMIT-001", "NUT-SAFE-PRE-001"}:
            assert rule["may_relax_golden_rules"] is False
    assert all("priority" not in r for r in reg["order"])                  # none invented without the base package
    assert len(order) == len(set(order))
