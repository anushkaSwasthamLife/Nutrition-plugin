"""Answers in Hindi and Roman Hindi (translate_reply.py): fixed reviewed text for emergencies, checked model translation for the rest."""
import json
import re

import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from dixit_answer.translate_reply import detect_language, reply_translations

TOKEN = re.compile(r"\{\{[A-Z]+\d*\}\}")


class TranslatingLLM(MockLLM):
    """Stands in for the model: a faithful 'translation' keeps digits, markers, bullets and bold marks and writes Devanagari."""
    name = "fake-translator"

    def __init__(self, mode="faithful"):
        self.mode, self.calls = mode, []

    def translate_lines(self, lines, language, failed=None):
        self.calls.append((list(lines), language, failed))
        out = []
        for line in lines:
            bullet = "- " if line.lstrip().startswith("- ") else ""
            digits = re.findall(r"\d+", TOKEN.sub(" ", line))
            if self.mode == "changes_number":
                digits = [str(int(d) + 1) for d in digits]
            body = "यह उत्तर हिंदी में दिया गया है" if language == "hi" else "yeh uttar Hindi mein diya gaya hai"
            if self.mode == "devanagari_in_hinglish":
                body = "यह उत्तर हिंदी में दिया गया है"
            if self.mode == "adds_food":
                body += " rajma"
            out.append(bullet + " ".join([body] + digits + TOKEN.findall(line)))
        return out


@pytest.fixture(scope="module")
def make(tmp_path_factory):
    def build(llm):
        return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("rt"), dish_lookup=False), llm=llm)
    return build


def test_language_detection():
    assert detect_language("पनीर में कितना प्रोटीन है", "paneer mein kitna protein hai", "en") == "hi"
    assert detect_language("mujhe chicken khana hai", "i want chicken", "en") == "hinglish"
    assert detect_language("can i eat paneer", "can i eat paneer", "en") == "en"
    assert detect_language("ok", "ok", "hinglish") == "hinglish"                      # a short reply keeps the language of the chat
    assert detect_language("can i eat paneer today", "can i eat paneer today", "hi") == "en"     # a real English message switches back


def test_english_chat_is_untouched(make):
    llm = TranslatingLLM()
    eng = make(llm)
    r = eng.answer("can i eat paneer")
    assert r.language == "en" and not llm.calls and not re.search(r"[ऀ-ॿ]", r.answer)


@pytest.mark.parametrize("message, language", [("मेरी छाती में दर्द हो रहा है", "hi")])
def test_emergency_uses_fixed_reviewed_hindi_and_never_the_model(make, message, language):
    llm = TranslatingLLM()
    r = make(llm).answer(message)
    assert r.type == "emergency" and not llm.calls
    hindi, english = r.answer.split("\n\n", 1)
    assert "112" in hindi and re.search(r"[ऀ-ॿ]", hindi)
    assert "112" in english and not re.search(r"[ऀ-ॿ]", english)                    # the English reply stays below it
    entry = [v for v in reply_translations()["fixed"].values() if v["hi"] == hindi]
    assert entry and entry[0]["english"] == english


def test_self_harm_reply_in_hindi_keeps_the_helpline_number(make):
    r = make(TranslatingLLM()).answer("मैं जीना नहीं चाहता")
    assert r.type == "emergency" and "14416" in r.answer and "112" in r.answer and re.search(r"[ऀ-ॿ]", r.answer)


def last_turn(eng):
    rows = [json.loads(line) for line in open(eng.settings.audit_log, encoding="utf-8")]
    return [r for r in rows if r["kind"] == "turn"][-1]


def test_hindi_question_is_translated_and_checked(make):
    llm = TranslatingLLM()
    eng = make(llm)
    r = eng.answer("दाल में कितनी कैलोरी होती है")
    assert llm.calls and r.language == "hi" and re.search(r"[ऀ-ॿ]", r.answer)
    row = last_turn(eng)
    assert row["language"]["method"] == "model" and row["language"]["english_text"]
    assert row["versions"]["reply_translations"] == reply_translations()["version"]
    # the numbers shown are exactly the numbers of the English answer that passed every check
    assert sorted(re.findall(r"\d+", r.answer)) == sorted(re.findall(r"\d+", row["language"]["english_text"]))


def test_hinglish_question_gets_roman_hindi(make):
    llm = TranslatingLLM()
    r = make(llm).answer("dal mein kitni calories hoti hain")
    assert r.language == "hinglish" and llm.calls and not re.search(r"[ऀ-ॿ]", r.answer) and "uttar" in r.answer


@pytest.mark.parametrize("mode, reason", [("changes_number", "numbers"), ("devanagari_in_hinglish", "Devanagari"), ("adds_food", "foods")])
def test_a_bad_translation_falls_back_to_the_english_answer(make, mode, reason):
    llm = TranslatingLLM(mode)
    message = "dal mein kitni calories hoti hain"
    r = make(llm).answer(message)
    if mode == "adds_food":
        assert not re.search(r"yeh uttar", r.answer)
    else:
        assert not re.search(r"yeh uttar|यह उत्तर", r.answer)
    assert len(llm.calls) == 2 and llm.calls[1][2]                                   # one retry, told what was wrong
    assert any(reason.lower() in p.lower() for p in llm.calls[1][2])
    assert r.language == "en"


def test_no_model_means_english(make):
    r = make(MockLLM()).answer("dal mein kitni calories hoti hain")
    assert r.language == "en" and r.answer


def test_the_chat_keeps_its_language_for_a_short_reply(make):
    eng = make(TranslatingLLM())
    first = eng.answer("dal mein kitni calories hoti hain")
    second = eng.answer("ok", first.conversation_id)
    assert second.language in {"hinglish", "en"}
    back = eng.answer("what is in a masala dosa today", first.conversation_id)
    assert back.language == "en"


def test_every_fixed_reply_and_checkin_has_reviewed_hindi():
    rules = [json.load(open(p, encoding="utf-8")) for p in ("dixit_answer/data/emergency_rules.json", "dixit_answer/data/harmful_intent_rules.json")]
    english = {v["english"]: v for v in reply_translations()["fixed"].values()}
    for data in rules:
        for cat in data["categories"]:
            for field in ("reply", "checkin"):
                if cat.get(field):
                    assert cat[field] in english, f"{cat['id']} {field} has no Hindi entry"
                    assert english[cat[field]]["hi"] and english[cat[field]]["hinglish"]
    for entry in english.values():
        assert not re.search(r"[ऀ-ॿ]", entry["hinglish"])
        assert re.findall(r"\d+", entry["english"]) == re.findall(r"\d+", entry["hi"]) == re.findall(r"\d+", entry["hinglish"])


def test_production_refuses_unsigned_translations():
    from dixit_answer.loader import LoadError, load_swastham_data
    with pytest.raises(LoadError):
        load_swastham_data(load_settings(environment="production"))
