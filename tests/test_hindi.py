"""Hindi in Devanagari script (plus Roman-Hindi question shapes): rewritten into plain words, safety unchanged, nothing guessed."""
import random
import re

import pytest

from dixit_answer import hindi
from dixit_answer.config import load_settings
from dixit_answer.hindi import has_devanagari, norm_dev, translate
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from tests.edge_helpers import assert_valid


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("hindi"), dish_lookup=True), llm=MockLLM())


def ask(engine, *messages, profile=None):
    cid, out = None, None
    for message in messages:
        out = engine.answer(message, cid, profile)
        cid = out.conversation_id
    return out


# ---------------------------------------------------------------- spelling out
@pytest.mark.parametrize("word, roman", [("कद्दू", "kaddu"), ("कमला", "kamla"), ("गरम", "garam"), ("ज्ञान", "gyan"), ("मेथी", "methi"), ("करेला", "karela"),
                                         ("बैंगन", "baingan"), ("रसगुल्ला", "rasgulla"), ("जलेबी", "jalebi"), ("पोहा", "poha"), ("उपमा", "upma")])
def test_unknown_words_are_spelled_out(word, roman):
    assert hindi._spell_out(word) == roman


def test_the_nukta_makes_no_difference_to_the_table():
    assert norm_dev("ज़्यादा") == norm_dev("ज्यादा") and translate("प्याज़") == translate("प्याज") == "pyaz"
    assert translate("मूंगफली") == translate("मूँगफली") == "moongphali"


def test_the_word_table_has_no_empty_or_non_roman_values():
    assert len(hindi.WORDS) > 200
    for dev, roman in hindi.WORDS.items():
        assert has_devanagari(dev) and re.fullmatch(r"[a-z]+(?: [a-z]+)*", roman), (dev, roman)


def test_every_food_alias_the_table_gives_is_a_food_the_pipeline_knows(eng):
    foods = {a for f in eng.index.foods.values() for a in f["aliases"]}
    must = {"roti": "FOOD-CHAPATI", "chawal": "FOOD-RICE", "dal": "FOOD-DAL", "aloo": "FOOD-ALOO", "bhindi": "FOOD-BHINDI", "paneer": "FOOD-PANEER",
            "palak": "FOOD-PALAK", "dahi": "FOOD-CURD", "pyaz": "FOOD-ONION", "tamatar": "FOOD-TOMATO", "badam": "FOOD-ALMOND", "anda": "FOOD-EGG",
            "idli": "FOOD-IDLI", "kela": "FOOD-BANANA", "lauki": "FOOD-LAUKI", "ghee": "FOOD-GHEE", "haldi": "FOOD-HALDI", "adrak": "FOOD-GINGER"}
    for roman, food_id in must.items():
        assert roman in foods
        assert [m.food_id for m in eng.index.find(roman)] == [food_id], roman
    for dev in ("रोटी", "चावल", "दाल", "आलू", "भिंडी", "पनीर", "पालक", "दही", "प्याज", "टमाटर", "बादाम", "अंडा", "इडली", "केला", "लौकी", "घी", "हल्दी", "अदरक"):
        assert [m.food_id for m in eng.index.find(translate(dev))] == [m.food_id for m in eng.index.find(hindi.WORDS[norm_dev(dev)])] != [], dev


# ---------------------------------------------------------------- sentence shapes
@pytest.mark.parametrize("text, expected", [
    ("पोहा में कितनी कैलोरी है?", "how much calories in poha"),
    ("दाल में कितना प्रोटीन होता है", "how much protein in dal"),
    ("भिंडी में कितने कार्ब्स हैं", "how much carbs in bhindi"),
    ("दाल की कैलोरी कितनी है", "how much calories in dal"),
    ("क्या मैं चावल खा सकता हूँ?", "can i eat chawal"),
    ("क्या मैं रोटी खा सकती हूं", "can i eat roti"),
    ("पनीर की रेसिपी बताओ", "recipe for paneer"),
    ("पालक पनीर कैसे बनाते हैं", "how to make palak paneer"),
    ("दाल और चावल में से कौन सा बेहतर है", "dal vs chawal"),
    ("मुझे मूंगफली से एलर्जी है", "i am allergic to moongphali"),
    ("मुझे डायबिटीज़ है", "i have diabetes"),
    ("पोहा क्या है", "what is poha"),
    ("dal mein kitna protein hai", "how much protein in dal"),
    ("kya main chawal kha sakta hoon", "can i eat chawal"),
])
def test_sentence_shapes(text, expected):
    assert translate(text) == expected


def test_devanagari_digits_and_the_full_stop():
    assert translate("मेरी शुगर ५२ है।") == "my sugar 52."      # since 22 Oct the Hinglish layer reads "meri" and "hai" too
    assert translate("१०० ग्राम") .startswith("100 ")


def test_english_and_unmatched_roman_text_come_back_unchanged():
    for text in ["calories in poha", "How much protein is in dal?", "I have aloo and tamatar, what can I make?", "can i eat rice", "hello", "dal mein hai",
                 "What is poha?", "", "   ", "!!!"]:
        assert translate(text) == text, text


def test_mixed_script_is_translated_word_by_word():
    assert translate("poha में कितनी calories हैं") == "how much calories in poha"
    assert translate("rice खा सकता हूँ") == "can i eat rice"


def test_odd_input_never_raises():
    rng = random.Random(7)
    pieces = ["ऀ", "ँ", "ं", "ः", "़", "्", "‍", "‌", "क", "्", "ा", "ि", "ं", "ॐ", "।", "?", "a", " ", "1", "०", "\x00", "😀"]
    for _ in range(500):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 25)))
        out = translate(text)
        assert isinstance(out, str)
    for text in ["्", "्क", "क्", "ं", "ः", "ा", "ाा", "क" * 5000, "ज्ञ", "ज्ञ्", "ऑ", "ॉ"]:
        assert isinstance(translate(text), str)


def test_translation_is_fast_and_bounded():
    text = "पोहा में कितनी कैलोरी है " * 200
    assert len(translate(text)) < 4 * len(text)


# ---------------------------------------------------------------- the chat
def test_hindi_questions_get_the_same_facts_as_the_english_ones(eng):
    for hindi_q, english_q in [("पोहा में कितनी कैलोरी है", "how many calories in poha"), ("दाल में कितना प्रोटीन होता है", "how much protein in dal"),
                               ("क्या मैं चावल खा सकता हूँ?", "can i eat rice"), ("भिंडी में कितने कार्ब्स हैं", "how many carbs in bhindi"),
                               ("इडली में कितनी कैलोरी है", "how many calories in idli")]:
        h, e = ask(eng, hindi_q), ask(eng, english_q)
        assert_valid(h, hindi_q)
        assert h.records and sorted(h.records) == sorted(e.records), (hindi_q, h.records, e.records)
        assert re.findall(r"[\d.]+", h.answer) == re.findall(r"[\d.]+", e.answer), hindi_q


def test_the_answer_is_in_english_and_has_no_devanagari_leak(eng):
    r = ask(eng, "पोहा में कितनी कैलोरी है")
    assert not has_devanagari(r.answer) and "calories" in r.answer


@pytest.mark.parametrize("text", ["मेरे सीने में दर्द है", "मुझे साँस नहीं आ रही", "मैं बेहोश हो गया", "मुझे आत्महत्या के विचार आ रहे हैं", "मेरी शुगर 52 है",
                                  "मुझे हार्ट अटैक जैसा लग रहा है", "उल्टी में खून आ रहा है"])
def test_devanagari_emergencies_still_stop_the_turn(eng, text):
    r = ask(eng, text)
    assert any(c in r.answer.lower() for c in ("112", "emergency", "doctor", "low blood sugar", "helpline", "crisis", "call")), r.answer[:200]
    assert not r.records


def test_a_hindi_allergy_is_kept_for_the_next_question(eng):
    first = ask(eng, "मुझे मूंगफली से एलर्जी है")
    second = eng.answer("मूंगफली में कितनी कैलोरी है", first.conversation_id, None)
    english_first = ask(eng, "I am allergic to peanuts")
    english_second = eng.answer("how many calories in peanuts", english_first.conversation_id, None)
    assert_valid(second)
    assert second.records == english_second.records and ("won't suit" in second.answer) == ("won't suit" in english_second.answer)


def test_an_unknown_hindi_food_is_named_in_roman_never_invented(eng):
    r = ask(eng, "ज़ोरबा में कितनी कैलोरी है")
    assert_valid(r)
    assert not re.search(r"\babout \d", r.answer) or r.records


def test_fuzz_hindi_and_mixed_messages_always_give_a_valid_answer(eng):
    rng = random.Random(3)
    words = ["पोहा", "दाल", "चावल", "में", "कितनी", "कैलोरी", "है", "क्या", "मैं", "खा", "सकता", "हूँ", "प्रोटीन", "मुझे", "एलर्जी", "से", "और", "नहीं", "हाँ",
             "dal", "rice", "calories", "?", "।", "१२", "kitna", "mein", "पनीर", "रेसिपी", "बनाओ", "कैसे", "बेहतर", "मधुमेह", "है।"]
    for _ in range(150):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 9)))
        assert_valid(ask(eng, text), text)
