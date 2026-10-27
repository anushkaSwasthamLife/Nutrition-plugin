"""One plugin, many doctors: everything except the doctor pack is shared. A second doctor's programme (its own names, no rules of
its own, or only some rule types) must get none of Dixit's wording or rules, and Dixit's answers must not change."""
import copy
import threading

import pytest

from dixit_answer import programme as programmes
from dixit_answer.config import recipe_book_paths
from dixit_answer.consistency import find_problems
from dixit_answer.llm import MockLLM
from dixit_answer.main_pipeline import MainPipeline
from dixit_answer.pipeline import Engine

HEART = {"name": "Heart Care programme", "programme": "the Heart Care programme",
         "assistant": "the Heart Care nutrition assistant", "assistant_title": "Heart Care Assistant",
         "coach": "your Heart Care dietitian", "coach_bare": "Heart Care dietitian",
         "app": "the Heart Care app", "meals_heading": "How Heart Care meals work"}
DIXIT_ONLY = ("dixit", "nuts, salad and sprouts", "sprouts step", "55 minutes", "two meals a day", "nothing between meals",
              "fruit rule", "dixit coach")
MESSAGES = ["hello", "who are you", "how does the programme work", "can I eat mango", "can I eat paneer", "what can I make with aloo and onion",
            "none", "what should I eat for dinner", "can I snack between meals", "how much should I eat", "write me a meal plan",
            "calories in dal", "I keep getting headaches", "how much should I walk daily", "thanks"]


def add_pack(engine, monkeypatch, doctor_id, identity=None, rules=None, books=()):
    pack = {"doctor_name": "Dr. Heart", "approved_by": None, "approved_on": None, "default_programme": "heart",
            "programmes": {"heart": {"label": "Heart Care", "identity": identity or HEART, "texts": {}, "never_foods": [], "limits": [],
                                     "approved_recipes": [], "recipe_books": list(books), **({"rules": rules} if rules else {})}}}
    monkeypatch.setitem(engine.packs.packs, doctor_id, pack)
    return doctor_id


@pytest.fixture
def heart(engine, monkeypatch):
    return add_pack(engine, monkeypatch, "DR-HEART")


@pytest.fixture(scope="module")
def main(engine):
    return MainPipeline(engine)


def chat(main, doctor_id, *messages, profile=None):
    out, cid = [], None
    for m in messages:
        r = main.answer(m, cid, profile or {"allergies": [], "diet_type": "vegetarian"}, doctor_id=doctor_id)
        cid = r.conversation_id
        out.append(r)
    return out


def test_another_doctors_patients_never_see_dixit_wording_or_rules(main, heart):
    for message in MESSAGES:
        answer = chat(main, heart, message)[0].answer.lower()
        assert not [w for w in DIXIT_ONLY if w in answer], (message, answer[:300])


def test_the_programme_is_named_from_the_pack(main, heart):
    about = chat(main, heart, "who are you")[0]
    assert "the Heart Care nutrition assistant" in about.answer
    greeting = chat(main, heart, "hello")[0]
    assert "the Heart Care nutrition assistant" in greeting.answer and greeting.handled_by == "main"


def test_without_a_meal_order_a_programme_question_is_told_so(main, heart):
    r = chat(main, heart, "how does the programme work")[0]
    assert "hasn't set meal rules for the Heart Care programme" in r.answer and "your Heart Care dietitian" in r.answer
    assert "protocol_full" not in r.plan["blocks"] and r.plan["protocol_level"] == "none"


def test_without_a_status_food_rule_fruit_is_not_restricted_and_no_status_question(main, heart, engine):
    r = chat(main, heart, "can I eat mango")[0]
    assert "diabetes" not in r.answer.lower() and (r.plan.get("followup") or {}).get("id") != "FU-STATUS"
    assert "please ask your Heart Care dietitian how it fits your plan" in r.answer


def test_no_sprouts_question_after_a_recipe(main, heart):
    for r in chat(main, heart, "what can I make with aloo and onion", "none"):
        assert (r.plan.get("followup") or {}).get("id") != "FU-SPROUTS" and "sprouts" not in r.answer.lower()


def test_dixit_answers_are_unchanged(main):
    r = chat(main, "DR-DIXIT", "how does the programme work")[0]
    assert "**How Dixit meals work:**" in r.answer
    mango = chat(main, "DR-DIXIT", "can I eat mango")[0]
    assert (mango.plan.get("followup") or {}).get("id") == "FU-STATUS"            # Dixit's fruit rule asks for the diabetes status
    hello = chat(main, None, "who are you")[0]
    assert "the Dixit nutrition assistant" in hello.answer                         # the default doctor is Dixit


def test_a_pack_can_switch_on_only_some_rule_types(engine, main, monkeypatch):
    pid = add_pack(engine, monkeypatch, "DR-FRUIT-ONLY", rules={"data": "dixit_lifestyle", "rule_types": ["status_food_rule"]})
    mango = chat(main, pid, "can I eat mango")[0]
    assert (mango.plan.get("followup") or {}).get("id") == "FU-STATUS"            # the fruit rule is on
    explain = chat(main, pid, "how does the programme work")[0]
    assert "hasn't set meal rules" in explain.answer and "nuts" not in explain.answer.lower()     # the meal order is off


def test_two_doctors_patients_at_the_same_time(main, heart):
    results = {}

    def ask(doctor_id):
        for i in range(5):
            results[(doctor_id, i)] = chat(main, doctor_id, "who are you")[0].answer

    threads = [threading.Thread(target=ask, args=(d,)) for d in ("DR-DIXIT", heart)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all("Dixit" in a for (d, _), a in results.items() if d == "DR-DIXIT")
    assert all("Heart Care" in a and "Dixit" not in a for (d, _), a in results.items() if d == heart)


def test_the_page_info_names_the_doctors_programme(engine, heart):
    from fastapi.testclient import TestClient
    from dixit_answer.web import create_app
    client = TestClient(create_app(engine))
    info = client.get("/api/info", params={"doctor_id": heart}).json()["programme"]
    assert info["identity"]["assistant_title"] == "Heart Care Assistant" and info["rule_types"] == []
    assert client.get("/api/info").json()["programme"]["identity"]["assistant_title"] == "Dixit Nutrition Assistant"


def test_outside_a_request_the_default_doctors_programme_applies(engine):
    assert programmes.current().identity["name"] == "Dixit programme"
    with programmes.use(programmes.NEUTRAL):
        assert programmes.current().rules.types == frozenset() and programmes.current().fill("{programme}") == "your programme"
    assert programmes.current().identity["name"] == "Dixit programme"


def test_pack_problems_are_caught_at_start_up(engine):
    data = copy.deepcopy(engine.data)
    prog = data.doctor_packs["packs"]["DR-DIXIT"]["programmes"]["dixit_lifestyle"]
    prog["rules"] = {"data": "nobody", "rule_types": ["meal_order", "magic"]}
    prog["identity"]["nickname"] = "x"
    prog["texts"]["slogan"] = "x"
    problems = "\n".join(find_problems(data, engine.library, engine.index, "development"))
    for bit in ("rules data 'nobody'", "unknown rule type 'magic'", "unknown identity field 'nickname'", "unknown text 'slogan'"):
        assert bit in problems


@pytest.mark.skipif(not recipe_book_paths()["healthful_diet"].is_file(), reason="the book file is not on this machine")
def test_a_programme_gets_only_the_recipe_books_its_pack_lists(settings, monkeypatch):
    s = copy.copy(settings)
    s.recipe_books = {"healthful_diet": recipe_book_paths()["healthful_diet"]}
    engine = Engine(s, llm=MockLLM())
    main = MainPipeline(engine)
    plain = add_pack(engine, monkeypatch, "DR-PLAIN")
    with_book = add_pack(engine, monkeypatch, "DR-BOOK", books=["healthful_diet"])
    assert chat(main, plain, "methi paratha recipe")[0].badge is None or chat(main, plain, "methi paratha recipe")[0].badge["kind"] != "programme"
    r = chat(main, with_book, "methi paratha recipe")[0]
    assert r.badge["kind"] == "programme" and r.badge["text"] == "Recipe from Healthful Diet (test)"
    assert "A recipe from Healthful Diet, page" in r.answer
