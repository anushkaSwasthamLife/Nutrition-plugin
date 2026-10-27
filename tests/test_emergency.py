"""Step 0: emergency check (emergency.py + data/emergency_rules.json)."""
import json

import pytest

from dixit_answer.config import DATA_DIR
from dixit_answer.emergency import EmergencyRules

RULES = EmergencyRules(json.loads((DATA_DIR / "emergency_rules.json").read_text(encoding="utf-8")))
NEG = ["no", "not", "don't", "dont", "do not", "never", "without", "none"]


@pytest.mark.parametrize("message, category", [
    ("I want to kill myself", "E5"),
    ("jeena nahi chahti ab", "E5"),
    ("my throat is swelling after eating peanuts", "E3"),
    ("I can't breathe", "E3"),
    ("saans nahi aa rahi", "E3"),
    ("my father has chest pain", "E2"),
    ("seene mein dard ho raha hai", "E2"),
    ("सीने में दर्द है", "E2"),
    ("सीने में तेज़ दर्द हो रहा है", "E2"),                      # a word between chest and pain (clause word pair)
    ("seene mein bahut dard ho raha hai", "E2"),
    ("there is a sharp pain in the middle of my chest", "E2"),
    ("her face is drooping and speech is slurred", "E2"),
    ("my son fainted", "E6"),
    ("he is vomiting blood", "E6"),
    ("I am shaking and sweating", "E1"),
    ("I took insulin and now I feel dizzy", "E1"),
    ("sugar low ho gayi", "E1"),
    ("I'm pregnant and bleeding", "E7"),
    ("baby stopped moving", "E7"),
    ("my sugar has ketones", "E4"),
])
def test_categories(message, category):
    assert RULES.check(message, NEG).category == category


@pytest.mark.parametrize("message, value, category", [
    ("my sugar is 52", 52, "E1"),
    ("sugar 45 mg/dl, what should I eat", 45, "E1"),
    ("glucose 3.1 mmol", 55.8, "E1"),
    ("sugar 400 mg/dl", 400, "E4"),
    ("rbs 350", 350, "E4"),
    ("my fasting sugar was 110", 110, ""),
    ("my fasting sugar was 6.1 mmol", 109.8, ""),
])
def test_blood_sugar_readings(message, value, category):
    result = RULES.check(message, NEG)
    assert result.glucose_mg_dl == pytest.approx(value) and result.category == category


@pytest.mark.parametrize("message", [
    "add 50 g sugar to halwa?", "sugar 2 spoons in tea", "low sugar dessert ideas", "sugar free biscuits",
    "I have hypothyroidism", "is dal good for diabetes", "I am allergic to peanuts", "what fits my plan",
    "my chest is fine, but my knee has pain", "chest workout made my arms tight", "I make myself vomit after eating",
])
def test_food_talk_is_not_an_emergency(message):
    assert not RULES.check(message, NEG).is_emergency


def test_negated_symptom_does_not_stop_the_turn_but_is_noted():
    result = RULES.check("I have no chest pain, can I eat dal?", NEG)
    assert not result.is_emergency and "chest pain" in result.negated
    assert RULES.check("no chest pain but I fainted", NEG).category == "E6"


def test_highest_priority_wins_and_all_matches_are_kept():
    result = RULES.check("I want to die and I have chest pain", NEG)
    assert result.category == "E5" and {m["category"] for m in result.matched} == {"E5", "E2"}


def test_every_reply_names_a_number_to_call():
    for cat in RULES.categories:
        assert "112" in cat["reply"] and "112" in cat["checkin"]
    assert "14416" in RULES.by_id["E5"]["reply"]


def test_emergency_turn_is_fixed_text_without_ai(engine, chat):
    calls = []
    original = engine.llm.reword
    engine.llm.reword = lambda *a, **k: calls.append(a) or original(*a, **k)
    try:
        r = chat("what can I cook with aloo and onion?", "my sugar is 48 and I feel shaky")[1]
    finally:
        engine.llm.reword = original
    assert r.type == "emergency" and r.plan["emergency_category"] == "E1"
    assert r.answer == RULES.by_id["E1"]["reply"] and not r.plan["followup"]
    assert all(c["passed"] for c in r.checks)


def test_next_answer_starts_with_one_checkin(chat):
    r = chat("I am shaking and sweating", "ok what can I cook with aloo and onion?", "what can I cook with paneer?")
    assert r[1].answer.startswith(RULES.by_id["E1"]["checkin"])
    assert RULES.by_id["E1"]["checkin"] not in r[2].answer


def test_negated_mention_adds_the_short_line(chat):
    r = chat("I have no chest pain, can I eat dal?")[0]
    assert r.type != "emergency" and RULES.negated_note in r.answer


def test_emergency_is_audited(engine, chat):
    chat("my throat is swelling after eating peanuts")
    last = [json.loads(line) for line in engine.settings.audit_log.read_text(encoding="utf-8").splitlines()][-1]
    assert last["emergency"]["category"] == "E3" and last["versions"]["emergency_rules"] == RULES.version


def test_production_refuses_unsigned_emergency_rules(monkeypatch, settings):
    from dataclasses import replace
    from dixit_answer import loader
    real = loader.json.loads
    def only_emergency_unsigned(text, *a, **k):        # every other file looks signed, so only this one can be refused
        data = real(text, *a, **k)
        if isinstance(data, dict) and "status" in data and data.get("version") != RULES.version:
            data = {**data, "status": "SIGNED"}
        return data
    monkeypatch.setattr(loader.json, "loads", only_emergency_unsigned)
    with pytest.raises(loader.LoadError, match="emergency_rules.json"):
        loader.load_swastham_data(replace(settings, environment="production"))
