"""Roman Hindi (Hinglish), clause by clause (hinglish.py + data/hinglish_glossary.json), through translate() and through the engine."""
import json
from pathlib import Path

import pytest

from dixit_answer import hinglish
from dixit_answer.config import DATA_DIR, load_settings
from dixit_answer.hindi import translate
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine

NUTRITION = Path(__file__).resolve().parents[2] / "Nutrition"
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")


@pytest.fixture(scope="module")
def eng_dishes(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("hinglish"), dish_lookup=True), llm=MockLLM())


@pytest.mark.parametrize("message, english", [
    ("mujhe chicken khana hai . so what can i have", "i want chicken. so what can i have"),
    ("mujhe chicken khana hai", "i want chicken"),
    ("chicken khana hai kya banau", "i want chicken, what can i cook"),
    ("main chicken khana chahta hoon, kya khau", "i want chicken, what can i have"),
    ("mujhe protein chahiye", "i want protein"),
    ("mujhe zyada protein chahiye", "i want high protein"),
    ("mujhe kam calorie wala khana batao", "i want low calorie food, what can i have"),
    ("mujhe kam carbs wala khana chahiye", "i want low carbs food, what can i have"),
    ("mujhe kuch halka khana hai", "i want something light"),
    ("meri bhookh lagi hai kya khau", "i am hungry, what can i have"),
    ("bhookh lagi hai", "i am hungry"),
    ("aaj kya banau", "what can i cook today"),
    ("breakfast ke liye kya khau", "for breakfast, what can i have"),
    ("dinner mein kya khaun", "for dinner, what can i have"),
    ("aaj chicken banau ya fish", "i want chicken or fish, what can i cook"),
    ("mujhe chicken ya fish khana hai", "i want chicken or fish"),
    ("iske alawa kuch aur batao", "something else"),
    ("kuch aur batao", "something else"),
    ("koi doosra option batao", "something else"),
    ("mera fish khane ka mann hai", "i want fish"),
    ("main chicken nahi khata", "i dont eat chicken"),
    ("chicken healthy hai kya", "is chicken good for me"),
    ("kya fish accha hai", "is fish good for me"),
    ("mujhe diabetes hai kya khau", "i have diabetes, what can i have"),
])
def test_roman_hindi_becomes_english(message, english):
    assert translate(message) == english


@pytest.mark.parametrize("message", [
    "so what can i have", "main course options", "i want chicken, what can i have", "give me protein", "what can I eat today", "do you have paneer",
    "man oh man", "low calorie dinner ideas", "can i eat rice and dal", "ok", "yes", "no", "calories in poha", "i have diabetes, what should i eat",
    "i am hungry what can i have", "nahi", "haan", "haan ji", "ji nahi",        # bare yes / no replies are read as they are
])
def test_english_and_bare_replies_are_left_alone(message):
    assert translate(message) == message


@pytest.mark.parametrize("message", ["", "   ", "?", ".", "mujhe", "kya", "hai hai hai", "x" * 3000, "mujhe " * 500])
def test_odd_input_never_breaks(message):
    assert isinstance(translate(message), str)


def test_mixed_messages_keep_their_english_clauses_and_punctuation():
    assert translate("Please tell me, mujhe chicken khana hai. Is it ok?") == "Please tell me, i want chicken. Is it ok?"
    assert translate("Hello! mujhe protein chahiye, but not too many calories") == "Hello! i want protein, but not too many calories"
    assert translate("mujhe chicken khana hai, lekin kam calories wala") == "i want chicken, but low calories"


def test_main_is_i_only_in_a_hindi_clause_and_never_before_course():
    assert translate("main chicken khana chahta hoon") == "i want chicken"
    assert translate("main course khana hai") == "i want main course"


def test_every_marker_is_not_an_english_word():
    english = {"man", "dil", "hum", "lag", "kha", "lu", "me", "main", "do", "so", "to", "the", "is", "na", "no", "ok", "ya", "hi", "he", "pi", "aa", "mat", "kar", "ko", "the"}
    glossary = hinglish.glossary()
    assert not (set(glossary["markers"]) & english - {"ya", "mat"}), set(glossary["markers"]) & english


def test_the_glossary_is_a_proposal_and_names_its_reviewer_slot():
    raw = json.loads((DATA_DIR / "hinglish_glossary.json").read_text(encoding="utf-8"))
    assert raw["status"] == "PROPOSED" and raw["signed_by"] is None and raw["version"].startswith("HG-")
    assert all(isinstance(v, str) and v for v in raw["words"].values())


def test_production_refuses_the_unsigned_glossary(monkeypatch, settings):
    from dataclasses import replace
    from dixit_answer import loader
    real = loader.json.loads

    def only_glossary_unsigned(text, *a, **k):
        data = real(text, *a, **k)
        if isinstance(data, dict) and "status" in data and data.get("version") != hinglish.glossary()["version"]:
            data = {**data, "status": "SIGNED"}
        return data
    monkeypatch.setattr(loader.json, "loads", only_glossary_unsigned)
    with pytest.raises(loader.LoadError, match="hinglish_glossary.json"):
        loader.load_swastham_data(replace(settings, environment="production"))


# ---------------------------------------------------------------- through the engine
NONVEG = {"allergies": ["peanut"], "diet_type": "non_vegetarian", "conditions": ["obesity"]}


def ask(engine, *messages, profile=NONVEG):
    cid, out = None, []
    for message in messages:
        r = engine.answer(message, cid, profile)
        cid = r.conversation_id
        out.append(r)
    return out


@needs_data
def test_the_22_oct_screenshot_message(eng_dishes):
    r = ask(eng_dishes, "mujhe chicken khana hai . so what can i have")[0]
    assert r.type == "target_meal_search" and r.plan["target_include"] == ["chicken"] and "Dishes with chicken" in r.answer


@needs_data
@pytest.mark.parametrize("message, kind", [
    ("mujhe protein chahiye", ("protein_g", "high")), ("mujhe kam calorie wala khana batao", ("energy_kcal", "low")),
    ("mujhe kuch halka khana hai", ("energy_kcal", "low")), ("mujhe zyada protein chahiye", ("protein_g", "high")),
])
def test_hinglish_wishes_become_searches(eng_dishes, message, kind):
    r = ask(eng_dishes, message)[0]
    assert r.type == "target_meal_search" and kind in {(t["field"], t["mode"]) for t in r.plan["targets"]}, (message, r.type)


@needs_data
def test_a_hinglish_follow_up_continues_the_search(eng_dishes):
    r = ask(eng_dishes, "mujhe protein chahiye", "kuch aur batao", "iske alawa kuch aur batao")
    assert r[1].type == "target_meal_search" and r[1].plan["target_mode"] == "more"
    assert r[2].type == "target_meal_search" and r[2].plan["target_mode"] == "more"


@needs_data
def test_a_hinglish_dont_eat_statement_is_noted(eng_dishes):
    r = ask(eng_dishes, "main chicken nahi khata")[0]
    assert r.type != "target_meal_search"


@needs_data
def test_the_safety_check_still_sees_hinglish_emergencies(eng_dishes):
    assert ask(eng_dishes, "mujhe saans nahi aa rahi")[0].type == "emergency"
    assert ask(eng_dishes, "mujhe jeena nahi chahiye")[0].type == "emergency"


@needs_data
def test_a_devanagari_sugar_reading_is_still_an_emergency(eng_dishes):
    r = ask(eng_dishes, "मेरी शुगर ५२ है।")[0]
    assert r.type == "emergency" and r.plan["emergency_category"] == "E1"


@pytest.mark.parametrize("message", ["mujhe jeena nahi chahiye", "ab jeene ka mann nahi hai", "mujhe jeena nahi hai", "jeene ki ichha nahi", "main jeena nahi chahte"])
def test_more_hindi_ways_to_say_i_do_not_want_to_live_are_emergencies(message):
    from dixit_answer.emergency import EmergencyRules
    rules = EmergencyRules(json.loads((DATA_DIR / "emergency_rules.json").read_text(encoding="utf-8")))
    assert rules.check(message, ["no", "not", "nahi"]).category == "E5"


@pytest.mark.parametrize("message", ["i dont eat chicken", "no chicken please", "i am allergic to prawns", "without fish", "is chicken healthy", "can i eat fish"])
def test_a_negated_or_permission_message_is_not_a_meat_wish(message):
    from dixit_answer.targets import ingredient_wish
    assert ingredient_wish(message) == [] and ingredient_wish(message, follow_up=True) == []
