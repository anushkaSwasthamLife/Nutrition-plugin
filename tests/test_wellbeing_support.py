"""A person who says they feel anxious, stressed or low gets care and approved wording, never a food claim (item: wellbeing support).

Before 16 Oct 2026 "I am feeling anxious, what should I eat?" got the whole programme explanation, and "my heart is racing, I'm anxious" got
"I can only help with food".
"""
import json
import re
from pathlib import Path

import pytest

from edge_helpers import assert_valid

DATA = Path(__file__).resolve().parent.parent / "dixit_answer" / "data"

FEELINGS = ["I am feeling anxious, what should I eat?", "i feel anxiety and stress, any food that helps", "I have panic attacks, what to eat",
            "feeling stressed and cant sleep", "I am very anxious and my heart is racing", "anxiety diet", "I feel low and sad today",
            "I'm so nervous", "I am overwhelmed", "I am worried all the time", "mujhe bahut ghabrahat ho rahi hai", "bahut tanav hai",
            "I have depression", "feeling hopeless today", "I'M ANXIOUS!!!", "anxious 😟", "does magnesium help with anxiety?",
            "which food cures anxiety", "can ashwagandha reduce stress", "stress eating how to stop"]
CLAIM_WORDS = ["magnesium", "omega", "ashwagandha", "turmeric", "calming", "calm you", "relax", "cure", "will help your anxiety", "reduce anxiety",
               "lower anxiety", "boost", "mood food", "serotonin", "supplement a", "take "]


@pytest.mark.parametrize("text", FEELINGS)
def test_a_feeling_is_met_with_support_not_the_programme_or_a_refusal(engine, text):
    r = engine.answer(text, None, {"allergies": []})
    assert_valid(r, text)
    assert r.type == "support_wellbeing", (text, r.type)
    assert r.answer.startswith("I'm sorry you're feeling this way.")
    assert "Tele-MANAS on 14416" in r.answer and "call 112" in r.answer
    assert "How Dixit meals work" not in r.answer and "I can only help with food" not in r.answer


@pytest.mark.parametrize("text", FEELINGS)
def test_no_food_claim_is_ever_made(engine, text):
    low = engine.answer(text, None, {"allergies": []}).answer.lower()
    for word in CLAIM_WORDS:
        assert word not in low, (text, word)
    assert "food can't treat anxiety" in low


def test_the_words_come_only_from_the_approved_file(engine):
    content = json.loads((DATA / "support_content.json").read_text(encoding="utf-8"))["topics"]["wellbeing"]
    approved = {content["opening"], content["limits"], content["help"], content["urgent"], content["bridge"], "Everyday habits that are recommended:",
                *[f["text"] for f in content["facts"]]}
    r = engine.answer("I am anxious", None, {"allergies": []})
    lines = [line[2:] if line.startswith("- ") else line for line in r.answer.splitlines()]
    assert set(lines) == approved


def test_every_fact_has_a_quote_a_source_a_link_and_a_licence_and_the_quote_supports_it():
    doc = json.loads((DATA / "support_content.json").read_text(encoding="utf-8"))
    assert doc["status"] == "PROPOSED" and doc["version"].startswith("SC-")
    for fact in doc["topics"]["wellbeing"]["facts"]:
        assert all(fact.get(k) for k in ("id", "text", "quote", "source", "url", "licence")), fact
        assert fact["url"].startswith("https://www.nimh.nih.gov/") and "public domain" in fact["licence"].lower()
        words = lambda t: {w for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 4}
        assert words(fact["quote"]) & words(fact["text"]), fact       # the sentence shown is about what the quote says


def test_no_ai_is_used_for_this_answer(settings):
    from test_checks_and_ai import ScriptedLLM
    from dixit_answer.pipeline import Engine
    import copy
    llm = ScriptedLLM([RuntimeError("the model must not be called")])
    r = Engine(copy.copy(settings), llm=llm).answer("I am anxious", None, {"allergies": []})
    assert r.type == "support_wellbeing" and not r.ai_used and not r.fallback and llm.calls == []


def test_the_checks_all_pass_on_the_fixed_text(engine):
    r = engine.answer("I am so stressed", None, {"allergies": []})
    assert not [c for c in r.checks if not c["passed"]] and not r.fallback


# ---------------------------------------------------------------- safety comes first
@pytest.mark.parametrize("text,category_word", [
    ("I feel anxious and I want to die", "Tele-MANAS"), ("I am anxious and have chest pain", "call 112"),
    ("so stressed, I fainted", "call 112"), ("I'm anxious and can't breathe", "call 112"), ("anxious, I want to kill myself", "Tele-MANAS"),
    ("panic attack and my sugar is 45", "blood sugar")])
def test_an_emergency_in_the_same_message_wins(engine, text, category_word):
    r = engine.answer(text, None, {"allergies": []})
    assert r.type == "emergency" and category_word.lower() in r.answer.lower(), (text, r.answer)


@pytest.mark.parametrize("text", ["I am anxious, I have lauki and tomato, what can I cook?", "stressed, how do I make palak paneer?",
                                  "I have anxiety and I am obese, what sabji should I eat", "I'm stressed. Can I have a banana?",
                                  "feeling nervous, how many carbs in one chapati"])
def test_a_food_question_with_a_feeling_gets_a_kind_line_and_the_real_answer(engine, text):
    r = engine.answer(text, None, {"allergies": []})
    assert r.type != "support_wellbeing" and r.answer.startswith("I'm sorry you're feeling this way.")
    assert "Tele-MANAS" in r.answer
    assert_valid(r, text)


def test_the_food_answer_that_follows_still_respects_the_profile(engine):
    r = engine.answer("I am anxious, I have lauki and paneer, what can I cook?", None, {"allergies": ["milk"], "conditions": ["hypertension"]})
    assert "left out paneer" in r.answer and "Kept in mind for you" in r.answer and "Tele-MANAS" in r.answer


# ---------------------------------------------------------------- it must not catch ordinary food questions
@pytest.mark.parametrize("text", ["I have lauki and tomato, what can I cook?", "how do I make palak paneer?", "what sabji can I eat?",
                                  "can I have a banana?", "how many carbs in one chapati?", "Which phone should I buy?", "I have obesity",
                                  "i have obesity , so i need a healthy meal . what sabji would u recommend", "hello", "I feel very hungry between meals",
                                  "I feel tired after lunch", "my stomach hurts after eating cabbage", "what is a stress test"[:0] or "Can I snack between meals?"])
def test_ordinary_questions_are_not_taken_for_a_feeling(engine, text):
    assert engine.answer(text, None, {"allergies": []}).type != "support_wellbeing", text


def test_the_chat_continues_normally_after_a_supportive_answer(engine):
    first = engine.answer("I am anxious", None, {"allergies": []})
    second = engine.answer("I have lauki and tomato, what can I cook?", first.conversation_id, {"allergies": []})
    assert second.type == "meal_from_ingredients" and "**You need**" in second.answer and "I'm sorry" not in second.answer


def test_the_support_file_is_unsigned_so_production_refuses_it():
    from dixit_answer import readiness
    result = readiness.check()
    assert any(i["area"] == "support_content" for i in result["items"])
    assert result["files"]["support_content"] == "PROPOSED"


def test_a_junk_profile_does_not_matter(engine):
    r = engine.answer("I am anxious", None, {"allergies": [5, None, "<b>"], "conditions": {"a": 1}})
    assert r.type == "support_wellbeing" and "<b>" not in r.answer
