"""Edge cases for the emergency check (step 0): false alarms, misses, negation, readings, odd input, speed, and the full pipeline."""
import json
import random
import string
import time

import pytest

from dixit_answer.config import DATA_DIR
from dixit_answer.emergency import EmergencyRules

RULES = EmergencyRules(json.loads((DATA_DIR / "emergency_rules.json").read_text(encoding="utf-8")))
NEG = ["no", "not", "don't", "dont", "do not", "never", "without", "none", "nope", "neither", "nor"]


def cat(text):
    return RULES.check(text, NEG).category


# Everyday food and health questions that must never be answered with an emergency message.
NORMAL = [
    "what can I cook with aloo and onion?", "can I eat rice at night", "is paneer good for diabetes", "how much dal should I eat", "give me a bhindi recipe",
    "I have high BP, what should I avoid", "is a low sugar diet good for me", "sugar free biscuits ok?", "add 2 tsp sugar to tea?", "how many calories in 1 roti",
    "I have diabetes and my fasting sugar is 110", "my sugar was 126 this morning", "my sugar is 98", "post meal sugar 140", "glucose 5.5 mmol", "HbA1c 6.5 normal?",
    "what is hypothyroidism", "I have hypothyroidism, can I eat soy", "I am allergic to peanuts, what can I cook", "is milk allergy common", "gluten free roti options",
    "I feel hungry in the evening", "I feel tired after lunch", "my stomach hurts after eating cabbage", "I have a headache, can I drink tea", "I get gas after rajma",
    "cough and cold, what to eat", "I have fever, light food ideas", "I am pregnant, can I eat papaya", "pregnant and want a healthy sabji", "breastfeeding diet",
    "heart healthy foods", "good fats for the heart", "my cholesterol is high, what to eat", "blood pressure diet", "salt intake per day",
    "can I eat 50 g sugar", "can I have 30 g jaggery", "sugar 2 spoons in my tea", "how much sugar is in a mango", "low sugar fruits",
    "what is a seizure", "my friend has epilepsy, any food tips", "kidney stone diet", "I have kidney disease, can I eat banana", "dialysis diet plan please",
    "I ate a lot yesterday, how do I restart", "I want to lose weight fast", "this halwa is to die for",
    "chest day at the gym, what protein", "I workout my chest and arms, what to eat", "my back hurts after sitting", "sore throat remedy", "throat feels dry",
    "tongue twister recipes", "lips are dry in winter", "face wash tips", "my face is dull, any food for glowing skin", "swollen ankles diet",
    "I fainted a few weeks ago, my doctor said it was fine, can I eat dal", "hello", "thanks", "ok", "namaste", "good morning dixit",
    "what is the dixit programme", "two meals a day or three", "can I have fruit after dinner", "is curd ok with fish", "tell me about nuts",
    "how to cook moong dal", "dal tadka recipe", "palak paneer ke liye kya chahiye", "mere paas aloo aur pyaaz hai", "kya main chawal kha sakta hoon",
    "sugar kam kaise karein", "bp ke liye kya khana chahiye", "diabetes mein roti kitni khaye", "pet dard hai kya khaun", "bukhar mein kya khana chahiye",
    "paneer allergy nahi hai", "no allergies", "none", "no", "yes", "I take metformin, any food advice", "I take insulin, what should I eat",
    "my father had a heart attack last year, what should he eat now", "sugar level 100 morning", "BG 105", "rbs 130 after lunch",
    "cheese, paneer and curd are dairy", "snack ideas for kids", "recipes for toddlers", "picky eater child ideas", "what is anaphylaxis",
    "my sugar is 70", "my sugar is 71", "sugar 299", "fasting 250 is that high", "I measured 180 after food",
]

# Real emergencies in the ways people actually write them.
TRUE = [
    ("I have chest pain", "E2"), ("CHEST PAIN!!!", "E2"), ("chest-pain since morning", "E2"), ("pain in my chest and left arm", "E2"),
    ("my chest is hurting a lot", "E2"), ("it feels like a heart attack", "E2"), ("seene mein dard ho raha hai", "E2"), ("chhati me dard", "E2"), ("सीने में दर्द है", "E2"),
    ("my mother's face is drooping and speech is slurred", "E2"), ("I think it's a stroke", "E2"), ("he is weak on one side", "E2"),
    ("I can't breathe", "E3"), ("cant breathe properly", "E3"), ("I can’t breathe", "E3"), ("difficulty breathing after eating", "E3"), ("trouble breathing", "E3"),
    ("my throat is closing up", "E3"), ("throat swelling after eating prawns", "E3"), ("lips are swelling after peanuts", "E3"), ("my tongue is swelling", "E3"),
    ("severe allergic reaction to nuts", "E3"), ("anaphylaxis, where is the epipen", "E3"), ("saans nahi aa rahi", "E3"), ("saans lene mein taklif hai", "E3"),
    ("my son fainted", "E6"), ("she passed out in the kitchen", "E6"), ("he is unconscious", "E6"), ("he won't wake up", "E6"), ("my father had a seizure", "E6"),
    ("having convulsions", "E6"), ("vomiting blood", "E6"), ("there is blood in my vomit", "E6"), ("took too many tablets", "E6"), ("he drank poison", "E6"), ("behosh ho gaya", "E6"),
    ("I want to die", "E5"), ("I want to kill myself", "E5"), ("I am going to end my life", "E5"), ("thinking of suicide", "E5"), ("I want to hurt myself", "E5"),
    ("better off dead", "E5"), ("I've been making myself vomit after meals", "E5"), ("jeena nahi chahti", "E5"), ("I don't want to die but I want to hurt myself", "E5"),
    ("there is no point, I want to die", "E5"), ("no one cares, I want to die", "E5"), ("nothing helps. I want to kill myself", "E5"),
    ("I am shaking and sweating", "E1"), ("shaky and sweaty after skipping lunch", "E1"), ("having a hypo", "E1"), ("sugar is very low", "E1"), ("my sugar dropped", "E1"),
    ("sugar 52", "E1"), ("sugar is 45 mg/dl", "E1"), ("glucose 3.1 mmol", "E1"), ("rbs 55", "E1"), ("bg 40", "E1"), ("my sugar is 69", "E1"), ("sugar 3.8", "E1"),
    ("I took insulin and feel dizzy", "E1"), ("I am diabetic and feeling faint", "E1"), ("sugar low ho gayi", "E1"),
    ("I'm pregnant and bleeding", "E7"), ("pregnant with severe pain", "E7"), ("my baby stopped moving", "E7"), ("waters broke early", "E7"),
    ("sugar 400", "E4"), ("sugar is 350 mg/dl", "E4"), ("glucose 20 mmol", "E4"), ("rbs 300", "E4"), ("sugar bahut high", "E4"), ("I have ketones", "E4"), ("fruity breath and vomiting", "E4"),
    ("First I ate dal. Then I got chest pain. What now?", "E2"), ("hi\n\nchest pain", "E2"), ("whatever you say but I can't breathe", "E3"),
]

# By design these are answered with the emergency message, because a missed emergency costs more than one unneeded message.
KNOWN_OVER_TRIGGERS = [
    "I want to die of laughter at this recipe",          # self-harm is never set aside, even for an idiom
    "my chest hurts when I laugh, any food tips",        # a symptom described in the present, even if mild
    "he is choking on a joke, what do I cook",
]

# A symptom that is only talked about: no emergency message, but the normal answer ends with "call 112 if that changes".
TALKED_ABOUT = [
    "what to eat after a stroke", "heart attack diet", "what is a seizure diet", "foods to avoid a hypo", "is popcorn a choking hazard for my toddler",
    "heat stroke prevention drinks", "what is anaphylaxis", "what is a seizure", "what are the signs of a heart attack", "define stroke",
    "my father had a heart attack last year, what should he eat now", "I fainted a few weeks ago, my doctor said it was fine, can I eat dal",
    "she had a stroke two years ago, diet tips", "history of seizures in my family, diet", "diet after a heart attack", "recipes to prevent a stroke",
]

# Looks like the above but is happening: must still be an emergency.
STILL_EMERGENCIES = [
    "what is this chest pain", "what is happening, I can't breathe", "what is this, I can't breathe", "I got chest pain after eating dal", "chest pain after a meal",
    "my father had a heart attack last year and now he has chest pain", "I have a history of heart problems and now chest pain", "I fainted a few weeks ago and just fainted again",
    "diet is fine but I have chest pain now", "what to eat? I have chest pain right now", "heart attack, help", "I think I am having a heart attack", "he is having a seizure now",
    "she had a stroke, call an ambulance", "after dinner I got chest pain", "what is anaphylaxis? my throat is closing", "avoid the stroke: face drooping now",
    "I fainted 3 hours ago and still feel faint, dizzy and shaky and sweating",
]


@pytest.mark.parametrize("text", NORMAL)
def test_everyday_questions_are_never_an_emergency(text):
    result = RULES.check(text, NEG)
    assert not result.is_emergency, (text, result.matched)


@pytest.mark.parametrize("text, expected", TRUE)
def test_real_emergencies_are_caught_with_the_right_category(text, expected):
    result = RULES.check(text, NEG)
    assert result.category == expected or expected in {m["category"] for m in result.matched}, (text, result.matched)


@pytest.mark.parametrize("text", KNOWN_OVER_TRIGGERS)
def test_known_over_triggers_are_documented_and_stay_that_way_until_a_doctor_decides(text):
    assert RULES.check(text, NEG).is_emergency


@pytest.mark.parametrize("text", TALKED_ABOUT)
def test_a_symptom_that_is_only_talked_about_does_not_trigger_the_emergency_message(text):
    result = RULES.check(text, NEG)
    assert not result.is_emergency and result.negated, (text, result.matched)


@pytest.mark.parametrize("text", STILL_EMERGENCIES)
def test_look_alikes_that_are_happening_are_still_emergencies(text):
    assert RULES.check(text, NEG).is_emergency, text


# ---------------------------------------------------------------- negation
@pytest.mark.parametrize("text", [
    "no chest pain", "I do not have chest pain", "never had a seizure", "without fainting or chest pain", "no chest pain, can I eat dal?",
    "none of chest pain or breathlessness, only gas", "no, I have no chest pain",
])
def test_a_plainly_negated_symptom_is_not_an_emergency(text):
    result = RULES.check(text, NEG)
    assert not result.is_emergency and result.negated, (text, result.matched)


@pytest.mark.parametrize("text, expected", [
    ("no chest pain but I fainted", "E6"), ("no, I fainted yesterday", "E6"), ("I do not know what to do. I can't breathe", "E3"),
    ("not sure if it is chest pain", "E2"), ("never felt like this: chest pain", "E2"), ("no point, I want to die", "E5"), ("I don't want to die", "E5"),
    ("without help I can't breathe", "E3"), ("I have no appetite and chest pain", "E2"), ("none of my pills help, I fainted", "E6"),
    ("no sugar tablets but I took insulin and am shaky", "E1"),
    ("no chest pain I fainted", "E6"), ("no cough and I can't breathe", "E3"), ("no fever no cold and a seizure", "E6"),
])
def test_a_negation_in_another_clause_never_hides_a_real_emergency(text, expected):
    assert cat(text) == expected, text


def test_the_same_symptom_negated_and_not_negated_is_an_emergency():
    assert cat("I do not have chest pain. Actually I have chest pain now") == "E2"


def test_self_harm_is_never_negated():
    for text in ("no, I want to die", "I do not want to die but I want to kill myself", "never mind, kill myself"):
        assert cat(text) == "E5"


# ---------------------------------------------------------------- blood-sugar readings
@pytest.mark.parametrize("text, category", [
    ("sugar 70", ""), ("sugar 69", "E1"), ("sugar 69.9", "E1"), ("sugar 299", ""), ("sugar 300", "E4"), ("sugar 301", "E4"), ("sugar 19", ""), ("sugar 20", "E1"),
    ("sugar 900", "E4"), ("sugar 901", ""), ("sugar 0", ""), ("sugar 3.8", "E1"), ("sugar 3.9", ""), ("sugar 4.0", ""), ("sugar 4.2", ""),
    ("sugar 4.3", ""), ("glucose 16.6 mmol", ""), ("glucose 16.7 mmol/l", "E4"), ("glucose 2.9 mmol/L", "E1"), ("blood sugar was 52 today", "E1"),
    ("blood glucose reading: 48", "E1"), ("my blood sugar level is 410", "E4"), ("sugar 52g", ""), ("sugar 52 g", ""), ("sugar 52 grams", ""), ("sugar 30 ml", ""),
    ("2 sugar 52 spoons", ""), ("sugar 45 kcal", ""), ("sugar 45%", ""),
    ("sugar 90 and sugar 320 after meal", "E4"), ("sugar 45 then 180", "E1"), ("sugar 52 and I ate 50 g sugar", "E1"), ("sugar 110, sugar 105", ""),
    ("SUGAR 52", "E1"), ("Sugar: 52", "E1"), ("sugar=52", "E1"), ("sugar - 52", "E1"), ("sugar level is 52 now", "E1"), ("sugar ₹52", ""), ("sugar 5 2", ""),
])
def test_blood_sugar_readings_at_every_boundary(text, category):
    result = RULES.check(text, NEG)
    assert (result.category if result.category in ("E1", "E4") else "") == category, (text, result.glucose_mg_dl, result.matched)


# ---------------------------------------------------------------- odd input
@pytest.mark.parametrize("value", [None, "", " ", "\n\n", 5, 3.14, [], {}, ["chest pain"], b"chest pain", True])
def test_non_text_and_empty_input_never_crash(value):
    result = RULES.check(value, NEG)
    assert not result.is_emergency


def test_unicode_control_characters_and_invisible_marks_never_hide_a_symptom():
    assert cat("chest\u00a0pain") == "E2" and cat("chest\tpain") == "E2" and cat("chest\npain") == "E2"
    assert cat("CHEST PAIN") == "E2" and cat("Chest Pain.") == "E2" and cat("\u201cchest pain\u201d") == "E2"
    for invisible in ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"):
        assert cat(f"c{invisible}hest pain") == "E2" and cat(f"chest{invisible} pain") == "E2", repr(invisible)
    assert cat("\U0001f525 chest pain \U0001f525") == "E2" and cat("chest pain\x00") == "E2" and cat("\x00chest pain") == "E2"


def test_a_second_reading_without_the_word_sugar_is_a_known_limit():
    assert cat("fasting sugar 90 and after meal 320") == ""             # the 320 has no "sugar" in front of it; a doctor may want a wider rule


def test_spelling_slips_are_not_caught_which_is_the_known_limit_of_a_phrase_list():
    for typo in ("chet pain", "chest paiin", "cant brethe", "i want ro die"):
        assert cat(typo) == ""                                           # recorded so the doctor knows; a second opinion check is on the plan


def test_a_very_long_message_is_checked_quickly_and_the_emergency_at_the_end_is_found():
    filler = " ".join(random.Random(1).choice(["dal", "rice", "roti", "aloo", "sabji", "curd"]) for _ in range(40_000))
    started = time.perf_counter()
    result = RULES.check(filler + " . I can't breathe", NEG)
    assert result.category == "E3" and time.perf_counter() - started < 5


def test_random_text_never_crashes_and_random_letters_are_never_an_emergency():
    rng = random.Random(7)
    for _ in range(600):
        text = "".join(rng.choice(string.ascii_letters + string.digits + " .,;:!?-'\"\n\t़ा्िीुू😀") for _ in range(rng.randint(0, 200)))
        RULES.check(text, NEG)
    for _ in range(300):
        words = ["".join(rng.choice("bcdfghjklmnpqrstvwxz") for _ in range(rng.randint(2, 8))) for _ in range(rng.randint(1, 12))]
        assert not RULES.check(" ".join(words), NEG).is_emergency


def test_priority_order_and_every_match_is_kept():
    result = RULES.check("I want to die, I have chest pain, I fainted and sugar 45", NEG)
    assert result.category == "E5" and {m["category"] for m in result.matched} >= {"E5", "E2", "E6", "E1"}
    assert result.reply == RULES.by_id["E5"]["reply"]


def test_every_category_has_a_reply_a_checkin_a_call_number_and_a_priority():
    priorities = [c["priority"] for c in RULES.categories]
    assert priorities == sorted(priorities) and len(set(priorities)) == len(priorities)
    for c in RULES.categories:
        assert ("112" in c["reply"] or "14416" in c["reply"]) and "112" in c["checkin"] and c["sources"] and c["phrases"]
        assert c["id"] == "E5" or c.get("negatable", True)
    assert RULES.by_id["E5"]["negatable"] is False


# ---------------------------------------------------------------- through the whole pipeline
def test_an_emergency_still_records_the_risk_words_in_the_same_message(engine, chat):
    first, second = chat("I take insulin and I am shaking and sweating", "what can I cook with aloo and onion?")
    assert first.type == "emergency"
    state = engine.store.load(first.conversation_id)
    assert state.safety_status["diabetes_medicine"] == "yes"


def test_the_checkin_appears_once_and_only_within_three_turns(chat):
    r = chat("chest pain", "ok what can I cook with aloo?", "and with onion?", "and with palak?", "and paneer?")
    assert r[0].type == "emergency" and r[1].answer.startswith("Before we carry on")
    assert all("Before we carry on" not in x.answer for x in r[2:])
    r = chat("chest pain", "hello", "hello", "hello", "what can I cook with aloo?")
    assert sum("Before we carry on" in x.answer for x in r) == 1


def test_two_emergencies_in_a_row_each_get_their_own_fixed_reply_without_a_checkin_on_the_second(chat):
    a, b, c = chat("chest pain", "I can't breathe", "ok thanks")
    assert a.type == b.type == "emergency" and a.answer != b.answer and "Before we carry on" in c.answer
    assert "breath" in c.answer or "112" in c.answer


def test_a_negated_mention_adds_one_line_and_never_the_emergency_text(chat):
    r = chat("no chest pain, what can I cook with aloo?")[0]
    assert r.type != "emergency" and r.answer.count("call 112") == 1


def test_an_emergency_reply_is_never_changed_by_the_ai_or_the_checks(engine, chat):
    for text in ("I want to die", "I can't breathe", "chest pain", "he fainted", "sugar 45", "pregnant and bleeding", "sugar 400"):
        r = chat(text)[0]
        cat_id = r.plan["emergency_category"]
        assert r.type == "emergency" and r.answer == RULES.by_id[cat_id]["reply"] and not r.ai_used and r.plan["followup"] is None
        assert all(c["passed"] for c in r.checks)


def test_a_long_or_empty_message_gets_the_normal_handling(chat):
    assert chat("")[0].type == "invalid_message"
    assert chat("x" * 5000)[0].type == "invalid_message"
    assert chat("chest pain " + "x" * 30)[0].type == "emergency"


@pytest.mark.parametrize("text, expected", [
    ("मेरी शुगर 52 है", "E1"), ("मेरी शुगर ५२ है", "E1"), ("शुगर 45", "E1"), ("ग्लूकोज 400", "E4"), ("मेरी शुगर ४०० है", "E4"), ("मेरी शुगर 110 है", ""),
    ("शुगर 2 चम्मच", ""), ("दिल का दौरा पड़ा है", "E2"), ("मिर्गी का दौरा", "E6"), ("शुगर लो हो गई", "E1"), ("सीने में दर्द है", "E2"), ("मुझे सांस नहीं आ रही", "E3"),
])
def test_hindi_script_readings_and_phrases(text, expected):
    result = RULES.check(text, NEG)
    assert (result.category if result.category else "") == expected, (text, result.matched, result.glucose_mg_dl)


def test_the_rules_version_is_bumped_with_every_change():
    assert RULES.version == "EM-0.1.3"
