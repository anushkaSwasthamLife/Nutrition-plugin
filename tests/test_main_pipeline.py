"""The main pipeline (step 0 safety, step R router, main assistant) and the nutrition plugin's new steps: doctor packs (2, 4),
recipe tiers (5), scaling (6b), source badges (8) and the hand-off back to the main pipeline (9)."""
import copy
import dataclasses

import pytest
from fastapi.testclient import TestClient

from dixit_answer.consistency import find_problems
from dixit_answer.main_ai import MainAssistant
from dixit_answer.main_pipeline import MainPipeline
from dixit_answer.person import Person
from dixit_answer.recipes import recipe_badge
from dixit_answer.state import ChatState
from dixit_answer.web import create_app


@pytest.fixture(scope="module")
def main(engine):
    return MainPipeline(engine)


@pytest.fixture
def talk(main):
    def run(*messages, profile=None, doctor_id=None):
        results, cid = [], None
        for message in messages:
            result = main.answer(message, cid, profile, doctor_id=doctor_id)
            cid = result.conversation_id
            results.append(result)
        return results
    return run


@pytest.fixture
def doctor_picks(engine, monkeypatch):
    """A doctor pack that approves the first recipe (no real doctor has picked recipes yet)."""
    pack = copy.deepcopy(engine.packs.packs["DR-DIXIT"])
    pack["programmes"]["dixit_lifestyle"]["approved_recipes"] = ["RCP-SABJI-001"]
    monkeypatch.setitem(engine.packs.packs, "DR-TEST", pack)
    return "DR-TEST"


def run_recipes(engine, person, **kw):
    args = {"have": [], "dish_ids": [], "unknown_words": [], "suggestion": None, "wants_list": False, "home_foods": []}
    args.update(kw)
    return engine.recipes.run(person=person, **args)


# ---------------------------------------------------------------- step R: the router
@pytest.mark.parametrize("message,intent", [
    ("hello", "greeting"), ("thank you", "thanks"), ("who are you", "about"), ("how much should I walk every day", "exercise"),
    ("I can't sleep at night", "sleep"), ("I keep getting headaches", "symptom"), ("I am on insulin", "medicine"),
    ("write me a poem", "general"),
])
def test_not_nutrition_goes_to_the_main_assistant(talk, message, intent):
    r = talk(message)[0]
    assert (r.handled_by, r.type, r.route["intent"]) == ("main", "main_ai", intent)
    assert r.badge is None


@pytest.mark.parametrize("message,intent", [
    ("calories in dal", "food"), ("aloo pyaz ki sabji recipe", "food"), ("what should I eat for dinner", "nutrition_words"),
    ("I have diabetes", "condition_statement"),
])
def test_nutrition_goes_to_the_plugin(talk, message, intent):
    r = talk(message)[0]
    assert (r.handled_by, r.route["kind"], r.route["intent"]) == ("plugin", "nutrition", intent)
    assert r.audit["handoff"]["intent"] == intent


def test_a_dish_name_without_a_food_word_goes_to_the_plugin(engine):
    from dixit_answer.router import IntentRouter
    router = IntentRouter(engine.data.router_rules, engine.index, engine.words, ["chef special"], None)
    assert router.route("chef special please", ChatState("c")).as_dict() == {"kind": "nutrition", "intent": "dish",
                                                                             "resolved_earlier_message": False}
    finder = IntentRouter(engine.data.router_rules, engine.index, engine.words, [], lambda cleaned: ["x"] if "khichu" in cleaned else [])
    assert finder.route("khichu", ChatState("c")).intent == "dish"


def test_unsure_asks_one_question_and_yes_answers_the_first_message_in_the_plugin(talk):
    first, second = talk("what should I do about my diabetes", "yes")
    assert first.handled_by == "router" and first.type == "clarify" and "about food or your diet" in first.answer
    assert second.handled_by == "plugin" and second.route == {"kind": "nutrition", "intent": "clarified", "resolved_earlier_message": True}
    assert "fruit" in second.answer                      # the diabetes question itself was answered, not "yes"


def test_unsure_then_no_goes_to_the_main_assistant(talk):
    first, second = talk("is this healthy", "no")
    assert first.type == "clarify"
    assert (second.handled_by, second.route["intent"]) == ("main", "clarified")


def test_a_new_message_instead_of_yes_or_no_drops_the_question(talk):
    _first, second = talk("is this healthy", "calories in dal")
    assert second.handled_by == "plugin" and second.route["intent"] == "food"
    assert second.route["resolved_earlier_message"] is False


def test_a_yes_after_the_question_was_skipped_answers_nothing_earlier(talk):
    *_, late = talk("is this healthy", "calories in dal", "yes")
    assert late.route["intent"] != "clarified" and late.route["resolved_earlier_message"] is False


@pytest.mark.parametrize("pending_turn,now", [(3, 5), (3, 2)])
def test_a_yes_or_no_on_another_turn_than_the_question_is_not_its_answer(engine, pending_turn, now):
    from dixit_answer.router import IntentRouter
    router = IntentRouter(engine.data.router_rules, engine.index, engine.words, [], None)
    for reply in ("yes", "no"):
        state = ChatState("c1", turn=now, route_pending={"message": "is this healthy", "turn": pending_turn})
        assert router.route(reply, state).intent != "clarified"


def test_a_reply_to_the_plugins_question_stays_in_the_plugin_but_a_main_topic_does_not(talk):
    asked, reply = talk("what can I make with aloo and onion", "none")
    assert asked.plan.get("followup") and reply.route["intent"] == "plugin_reply" and reply.handled_by == "plugin"
    _asked, other = talk("what can I make with aloo and onion", "how much should I walk daily")
    assert other.handled_by == "main" and other.route["intent"] == "exercise"


def test_a_no_to_the_plugins_medicine_question_stays_in_the_plugin(talk):
    asked, reply = talk("how does the programme work", "no I don't take any medicine")
    assert (asked.plan.get("followup") or {}).get("id") == "FU-SAFETY"
    assert reply.handled_by == "plugin" and reply.route["intent"] == "plugin_reply" and "Thanks, noted." in reply.answer


@pytest.mark.parametrize("message", ["calries in poha", "protien in dal"])
def test_a_misspelt_nutrition_word_goes_to_the_plugin(talk, message):
    assert talk(message)[0].handled_by == "plugin"


def test_a_near_miss_with_a_different_start_is_not_a_nutrition_word(talk):
    assert talk("I am looking for a new job")[0].handled_by == "main"


@pytest.mark.parametrize("message", ["I work as a waiter", "I am travelling to Hungary next month", "I am a programmer",
                                     "the fastening on my bag broke", "where do I find a good dietician", "I booked a cab"])
def test_a_real_word_close_to_a_nutrition_word_is_not_a_typo(talk, message):
    assert talk(message)[0].handled_by == "main"


@pytest.mark.parametrize("message", ["calries in poha", "protien in dal", "is breakfst important", "how much fibbre in oats",
                                     "vitamns in spinach"])
def test_near_miss_typos_of_nutrition_words_still_go_to_the_plugin(talk, message):
    assert talk(message)[0].handled_by == "plugin"


@pytest.mark.parametrize("message", ["can I stop metformin if I follow this diet", "I want to stop my insulin and just follow the diet",
                                     "metformin band kar du kya diet se", "can I stop taking my bp medicine"])
def test_a_medicine_change_gets_the_medicine_line_even_with_a_food_word(talk, message):
    r = talk(message, profile={"allergies": [], "conditions": ["type 2 diabetes"]})[0]
    assert r.handled_by == "main" and r.route["intent"] == "medicine"
    assert "Please don't start, stop or change a medicine without your doctor." in r.answer


@pytest.mark.parametrize("message", ["can I skip lunch, I take insulin", "reduce oil, I take thyroxine", "can I eat mango with metformin"])
def test_a_food_change_next_to_a_medicine_is_still_a_food_question(talk, message):
    assert talk(message)[0].handled_by == "plugin"


@pytest.mark.parametrize("message", ["will this diet cure my diabetes", "can I reverse diabetes with this diet", "does turmeric cure cancer"])
def test_a_cure_question_gets_no_promise(talk, message):
    r = talk(message)[0]
    assert r.route["intent"] == "cure" and r.answer.startswith("I can't promise that a diet will cure or reverse a condition.")


@pytest.mark.parametrize("message", ["what should my 5 year old eat", "can I give honey to my 18 month old baby", "is ghee good for my son",
                                     "bachche ko kya khilau", "mera beta 5 saal ka hai, use kya khilau"])
def test_a_childs_diet_gets_the_adults_only_reply(talk, message):
    r = talk(message)[0]
    assert r.route["intent"] == "child" and "The programme and my answers are for adults." in r.answer


@pytest.mark.parametrize("message", ["what can I make for my kids tiffin", "my son is 25 years old, can he eat poha",
                                     "I am 40 years old, can I eat mango"])
def test_cooking_for_the_family_or_an_adult_is_not_a_childs_diet(talk, message):
    assert talk(message)[0].handled_by == "plugin"


def test_short_follow_up_after_a_nutrition_answer_stays_in_the_plugin(talk):
    _r, follow = talk("calories in dal", "why?")
    assert follow.handled_by == "plugin" and follow.route["intent"] == "continuation"


# ---------------------------------------------------------------- step 0: safety first
def test_emergency_is_answered_before_the_router(talk):
    r = talk("I have chest pain and can't breathe")[0]
    assert r.handled_by == "safety" and r.type == "emergency" and r.route["kind"] == "safety"
    assert "112" in r.answer and r.badge is None


def test_a_risk_word_sent_to_the_main_assistant_is_still_recorded(main, talk):
    r = talk("I am on insulin")[0]
    assert r.handled_by == "main" and "noted it" in r.answer
    assert "without your doctor" in r.answer                       # the locked medicine line
    assert main.engine.store.load(r.conversation_id).safety_status["diabetes_medicine"] == "yes"


def test_symptom_reply_carries_the_low_sugar_line(talk):
    r = talk("I feel dizzy")[0]
    assert r.handled_by == "main" and "shaky, sweaty or dizzy" in r.answer


# ---------------------------------------------------------------- the main assistant with a model
class FakeModel:
    name, model = "fake", "fake-1"

    def __init__(self, reply):
        self.text = reply

    def general_reply(self, request):
        return self.text


def test_main_assistant_uses_a_clean_model_reply(engine):
    out = MainAssistant(FakeModel("Gentle walks after meals can feel good. Ask your Dixit coach what suits you."),
                        engine.data.router_rules).reply("should I walk", "exercise")
    assert out.method == "model" and out.text.startswith("Gentle walks")


@pytest.mark.parametrize("bad", ["Take 500 mg twice a day.", "See https://example.com", "word " * 200, ""])
def test_main_assistant_refuses_numbers_links_and_long_replies(engine, bad):
    rules = engine.data.router_rules
    out = MainAssistant(FakeModel(bad), rules).reply("my medicine", "medicine")
    assert out.method == "template" and out.text.startswith(rules["main_replies"]["medicine"])
    assert rules["locked_lines"]["medicine"] in out.text


def test_symptom_model_reply_always_gets_the_locked_line(engine):
    rules = engine.data.router_rules
    out = MainAssistant(FakeModel("I'm sorry you feel unwell. Please talk to your doctor."), rules).reply("dizzy", "symptom")
    assert out.method == "model" and out.text.endswith(rules["locked_lines"]["symptom"])


@pytest.mark.parametrize("topic", ["medicine", "symptom", "general"])
@pytest.mark.parametrize("bad", ["You can stop your tablets once you feel better.", "Try to skip the evening tablet.",
                                 "Ask your doctor to increase the dose.", "Cut down on the pills slowly.", "Dawai band kar do."])
def test_a_model_reply_naming_a_medicine_change_gets_the_template(engine, topic, bad):
    rules = engine.data.router_rules
    out = MainAssistant(FakeModel(bad), rules).reply("my tablets", topic)
    assert out.method == "template" and out.text.startswith(rules["main_replies"][topic].split("{")[0])
    assert any("medicine change or dose" in p for p in out.problems)


def test_a_change_word_is_fine_outside_the_medicine_topics(engine):
    out = MainAssistant(FakeModel("Short walks are a good way to start, and you can increase them slowly."),
                        engine.data.router_rules).reply("should I walk", "exercise")
    assert out.method == "model"


# ---------------------------------------------------------------- steps 2, 4: doctor packs and constraint layers
def test_unknown_doctor_gets_the_default_pack_and_the_audit_says_so(talk):
    r = talk("aloo pyaz ki sabji recipe", doctor_id="DR-NOBODY")[0]
    assert r.audit["doctor_pack"]["doctor_id"] == "DR-DIXIT"
    assert "unknown doctor" in r.audit["doctor_pack"]["notes"][0]


def test_doctor_never_foods_are_blocked_first(engine):
    pack = dataclasses.replace(engine.packs.resolve(), never_foods=frozenset({"FOOD-ALOO"}))
    out = run_recipes(engine, Person(allergies=[], diet_type="vegetarian", doctor_pack=pack), have=["FOOD-ALOO", "FOOD-ONION"])
    assert ("FOOD-ALOO", "your doctor's programme") in out.left_out
    assert out.recipe is None or "FOOD-ALOO" not in {i["food"] for i in out.recipe.ingredients}


def test_doctor_limit_is_the_strictest_layer(engine):
    pack = dataclasses.replace(engine.packs.resolve(), limits=({"condition": "general", "nutrient": "energy_kcal", "daily_max": 500},))
    limits = engine.recipes.compile_limits(Person(allergies=[], conditions=["obesity"], doctor_pack=pack))
    assert limits["energy_kcal"].conditions == ["doctor pack DR-DIXIT"] and int(limits["energy_kcal"].limit_max) == 500
    loose = dataclasses.replace(pack, limits=({"condition": "general", "nutrient": "energy_kcal", "daily_max": 99999},))
    limits = engine.recipes.compile_limits(Person(allergies=[], conditions=["obesity"], doctor_pack=loose))
    assert limits["energy_kcal"].conditions == ["obesity"]          # a pack never relaxes the patient's own layer


# ---------------------------------------------------------------- step 5: recipes searched by tier
def test_doctor_approved_recipes_are_searched_first(full_engine):
    base = full_engine.packs.resolve()
    person = lambda approved: Person(allergies=[], diet_type="vegetarian",                          # noqa: E731
                                     doctor_pack=dataclasses.replace(base, approved_recipes=frozenset(approved)))
    plain = run_recipes(full_engine, person([]), have=["FOOD-ALOO", "FOOD-ONION"])
    ranked = run_recipes(full_engine, person(["RCP-SABJI-043"]), have=["FOOD-ALOO", "FOOD-ONION"])
    assert plain.choices[0].recipe_id != "RCP-SABJI-043"
    assert ranked.choices[0].recipe_id == "RCP-SABJI-043" and ranked.choices[0].tier == "doctor"


# ---------------------------------------------------------------- step 6b: changed by code (scale)
def test_a_dish_over_a_limit_is_scaled_and_badged_as_adapted(engine):
    pack = dataclasses.replace(engine.packs.resolve(), approved_recipes=frozenset({"RCP-SABJI-001"}))
    whole = engine.recipes._from_collection("RCP-SABJI-001", Person(doctor_pack=pack))
    engine.recipes.calculate(whole)
    kcal = float(whole.totals["energy_kcal"])
    tight = dataclasses.replace(pack, limits=({"condition": "general", "nutrient": "energy_kcal", "daily_max": kcal * 0.8},))
    out = run_recipes(engine, Person(allergies=[], diet_type="vegetarian", doctor_pack=tight), dish_ids=["RCP-SABJI-001"])
    assert out.status == "recipe" and out.recipe.gate.passed and out.recipe.scale is not None
    assert 0.5 <= float(out.recipe.scale) < 1 and "made smaller" in out.recipe.makes
    badge = recipe_badge(out.recipe)
    assert badge["kind"] == "adapted" and badge["text"].startswith("Adapted from Dr. Dixit's recipe")
    assert engine.recipes.label_for(out.recipe).startswith("Adapted for you from a recipe recommended by Dr. Dixit")


def test_a_dish_is_never_scaled_below_half(engine):
    pack = dataclasses.replace(engine.packs.resolve(), limits=({"condition": "general", "nutrient": "energy_kcal", "daily_max": 50},))
    out = run_recipes(engine, Person(allergies=[], diet_type="vegetarian", doctor_pack=pack), dish_ids=["RCP-SABJI-001"])
    assert out.recipe is None or out.recipe.recipe_id != "RCP-SABJI-001"


def test_an_allergen_failure_is_never_fixed_by_scaling(engine):
    whole = engine.recipes._from_collection("RCP-SABJI-001", Person(doctor_pack=engine.packs.resolve()))
    whole.ingredients.append({"food": "FOOD-PANEER", "grams": 50, "unit": ""})
    engine.recipes.calculate(whole)
    person = Person(allergies=["milk"], doctor_pack=engine.packs.resolve())
    limits = engine.recipes.compile_limits(person)
    gate = engine.recipes.gate(whole, person, limits, engine.allergy.blocked_foods(person))
    assert not gate.passed and engine.recipes.scale(whole, gate, limits) is None


# ---------------------------------------------------------------- steps 8, 9: badge and hand-back
def test_doctor_recipe_is_shown_with_the_doctors_badge(talk, doctor_picks):
    r = talk("aloo pyaz ki sabji recipe", doctor_id=doctor_picks)[0]
    assert r.badge == {"kind": "doctor", "text": "Recommended by Dr. Dixit (test)", "recipe_id": "RCP-SABJI-001", "tier": "doctor"}
    assert "Recommended by Dr. Dixit (test pack, not yet signed by the doctor)." in r.answer
    assert r.audit["badge"] == r.badge and r.audit["route"]["kind"] == "nutrition"


def test_without_a_doctor_pick_a_test_recipe_is_a_reference_recipe(talk):
    r = talk("aloo pyaz ki sabji recipe")[0]
    assert r.badge["kind"] == "reference" and "Swastham test recipe" in r.answer


def test_reference_recipe_badge(full_engine):
    r = MainPipeline(full_engine).answer("jeera aloo recipe")
    assert r.badge["kind"] == "reference" and r.badge["text"] == "Reference recipe (test)"
    assert "Swastham test recipe" in r.answer


def test_usda_facts_name_a_trusted_source(talk):
    r = talk("protein in almonds")[0]
    assert r.badge == {"kind": "nutrition_table", "text": "Nutrition values: USDA FoodData Central", "trusted": True}
    (source,) = r.sources
    assert source["trusted"] and source["name"].startswith("USDA FoodData Central") and source["used_for"] == "nutrition values"
    assert source["records"][0]["id"].startswith("FDC ID:") and "almonds" in source["records"][0]["food"].lower()
    assert r.audit["sources"][0]["trusted"] is True


def test_indian_library_facts_are_named_and_marked_as_development_data(talk):
    r = talk("calories in dal")[0]
    assert r.badge["kind"] == "nutrition_table_dev" and "dev data" not in r.badge["text"] and r.badge["trusted"] is False
    assert "dev data" not in r.answer                                      # the Sources panel names and marks it instead
    assert r.sources and all(not g["trusted"] for g in r.sources)
    assert r.sources[0]["name"] == "INDB-derived Swastham Indian Meal Nutrient Library" and "Licence not yet confirmed" in r.sources[0]["note"]


def test_recipe_answers_list_the_usda_records_behind_the_calculation(talk):
    r = talk("aloo pyaz ki sabji recipe")[0]
    assert r.sources and all(g["trusted"] and g["used_for"] == "recipe calculation" for g in r.sources)
    foods = " ".join(rec["food"].lower() for g in r.sources for rec in g["records"])
    assert "potatoes" in foods and "onions" in foods


def test_trust_follows_the_production_source_list(engine):
    from dixit_answer.sources import answer_sources
    draft = type("D", (), {"facts": [], "recipe": None})()
    assert answer_sources(draft, engine.library, engine.index, None, {"usda"}) == []
    fact = type("F", (), {"record_id": next(r for r in engine.library.records if engine.library.is_indian(r)), "food_name": "x"})()
    draft.facts = [fact]
    assert answer_sources(draft, engine.library, engine.index, None, {"usda"})[0]["trusted"] is False
    assert answer_sources(draft, engine.library, engine.index, None, {"usda", "indian"})[0]["trusted"] is True


def test_web_chat_returns_the_badge_and_route(engine, doctor_picks):
    client = TestClient(create_app(engine))
    body = client.post("/api/chat", json={"message": "aloo pyaz ki sabji recipe", "doctor_id": doctor_picks}).json()
    assert body["handled_by"] == "plugin" and body["badge"]["kind"] == "doctor" and body["route"]["kind"] == "nutrition"
    assert body["sources"] and body["sources"][0]["trusted"] is True
    body = client.post("/api/chat", json={"message": "hello"}).json()
    assert body["handled_by"] == "main" and body["badge"] is None


# ---------------------------------------------------------------- data and state
def test_consistency_catches_a_pack_naming_a_missing_recipe_or_food(engine):
    data = copy.deepcopy(engine.data)
    prog = data.doctor_packs["packs"]["DR-DIXIT"]["programmes"]["dixit_lifestyle"]
    prog["approved_recipes"].append("RCP-NOPE")
    prog["never_foods"].append("FOOD-NOPE")
    prog["limits"].append({"condition": "general", "nutrient": "magic_g", "daily_max": -1})
    problems = "\n".join(find_problems(data, engine.library, engine.index, "development"))
    assert "RCP-NOPE" in problems and "FOOD-NOPE" in problems and "magic_g" in problems and "bad daily_max" in problems


def test_production_refuses_an_unsigned_pack(engine):
    problems = find_problems(engine.data, engine.library, engine.index, "production")
    assert any("doctor pack DR-DIXIT: not signed" in p for p in problems)


def test_consistency_needs_the_medicine_check_on_model_replies(engine):
    data = copy.deepcopy(engine.data)
    del data.router_rules["model_reply_deny"]
    assert any("model_reply_deny" in p for p in find_problems(data, engine.library, engine.index, "development"))


def test_route_state_survives_a_round_trip_and_a_bad_value():
    state = ChatState("c1", last_route="clarify", route_pending={"message": "is this healthy", "turn": 3})
    again = ChatState.from_json(state.to_json())
    assert again.route_pending == {"message": "is this healthy", "turn": 3} and again.last_route == "clarify"
    bad = ChatState.from_json('{"route_pending": "oops", "last_route": 5}')
    assert bad.route_pending is None and bad.last_route == ""
