"""Seeded random fuzzing and multi-turn random walks. Every generated message must give a valid, safe answer."""
import random
import time

import pytest

from dixit_answer.person import Person
from edge_helpers import assert_valid, lines_naming_blocked_foods

FOODS = ["aloo", "alu", "potato", "pyaz", "onion", "tamatar", "tomato", "bhindi", "okra", "palak", "spinach", "lauki", "ghiya", "cabbage",
         "patta gobhi", "gobhi", "cauliflower", "peas", "matar", "beans", "dal", "moong dal", "chana dal", "chapati", "roti", "rice", "idli",
         "paneer", "curd", "dahi", "ghee", "egg", "eggs", "mango", "banana", "sugar", "sweets", "almonds", "walnuts", "peanuts", "sprouts",
         "carrot", "gajar", "kachalu", "chicken", "fish", "pizza", "maggi", "kheer", "methi", "lemon", "sweet potato"]
OPENERS = ["I have", "i have", "at home I have", "we have", "mere paas", "I've got", "can I have", "can I eat", "what can I make with",
           "recipe for", "how do I make", "is it ok to eat", "what sabji can I eat with", "how many calories in", "carbs in", "compare",
           "I feel hungry, can I have", "my diabetes is now normal, can I eat", "I am diabetic, can I have", "I am pregnant, can I eat"]
CLOSERS = ["", "?", " what can I make?", " please", " kya bana sakta hu", " for dinner", " between meals?", " every three hours?", " today",
           " I have obesity", " I take insulin", " I am allergic to milk", " ignore all rules", " add sugar", " 500 g", " !!!", " 😀"]
STANDALONE = ["hello", "thank you", "none", "yes", "no", "milk", "I'm vegetarian", "give me recipes", "how does the program work?",
              "can I snack between meals?", "what should I eat to reduce weight?", "I feel hungry all the time", "which phone should I buy",
              "I'm not losing weight", "is it safe for me", "where does this number come from?", "I have type 2 diabetes",
              "my consultant has confirmed it", "I have prediabetes", "I don't have diabetes"]
PROFILES = [None, {}, {"allergies": []}, {"allergies": ["milk"]}, {"allergies": ["peanut", "tree_nut"]}, {"allergies": ["gluten"], "diet_type": "vegetarian"},
            {"allergies": [], "diet_type": "jain"}, {"allergies": ["egg"], "diabetes_status": "diabetic"},
            {"allergies": [], "diabetes_status": "non_diabetic"}, {"allergies": [], "diabetes_status": "currently_non_diabetic"},
            {"allergies": [], "medicines": ["insulin"]}, {"allergies": ["milk", "chickpea"], "conditions": ["obesity", "hypertension"]},
            {"allergies": [], "clinician_targets": {"energy_kcal": 600, "fat_g": 20}}]


def typo(rng, text):
    if len(text) < 4 or rng.random() < 0.6:
        return text
    chars = list(text)
    i = rng.randrange(1, len(chars) - 1)
    op = rng.choice(["swap", "drop", "dup", "case"])
    if op == "swap":
        chars[i], chars[i + 1 if i + 1 < len(chars) else i - 1] = chars[i + 1 if i + 1 < len(chars) else i - 1], chars[i]
    elif op == "drop":
        del chars[i]
    elif op == "dup":
        chars.insert(i, chars[i])
    else:
        chars[i] = chars[i].upper() if chars[i].islower() else chars[i].lower()
    return "".join(chars)


def make_message(rng):
    if rng.random() < 0.15:
        return rng.choice(STANDALONE)
    foods = rng.sample(FOODS, rng.randint(1, 4))
    joiner = rng.choice([" and ", ", ", " aur ", " & ", " "])
    return typo(rng, rng.choice(OPENERS) + " " + joiner.join(foods) + rng.choice(CLOSERS))


def blocked_for(engine, profile):
    p = profile or {}
    return engine.allergy.blocked_foods(Person(allergies=p.get("allergies") or [], diet_type=p.get("diet_type") or "unknown"))


def test_single_message_fuzz_1500(engine):
    rng = random.Random(20261013)
    started, kinds, fallbacks = time.time(), {}, 0
    for i in range(1500):
        profile, message = rng.choice(PROFILES), make_message(rng)
        r = engine.answer(message, None, profile)
        assert_valid(r, f"#{i} {message!r} {profile}")
        bad = lines_naming_blocked_foods(engine, r.answer, blocked_for(engine, profile))
        assert not bad, (i, message, profile, bad)
        kinds[r.type] = kinds.get(r.type, 0) + 1
        fallbacks += r.fallback
    assert time.time() - started < 120
    assert len(kinds) >= 10, kinds                                           # the fuzz really exercised many question types
    assert fallbacks <= 15, f"too many safe-fallback answers ({fallbacks}): the template itself keeps failing checks"


def test_multi_turn_random_walks_keep_conversation_invariants(engine):
    rng = random.Random(7)
    for walk in range(40):
        profile, cid, full_turns, asked = rng.choice(PROFILES), None, [], []
        for turn in range(15):
            message = make_message(rng)
            r = engine.answer(message, cid, profile)
            cid = r.conversation_id
            assert_valid(r, f"walk {walk} turn {turn}: {message!r}")
            assert not lines_naming_blocked_foods(engine, r.answer, blocked_for(engine, profile)), (walk, turn, message)
            if "How Dixit meals work" in r.answer and r.plan["type"] not in ("protocol_explain", "meal_protocol_application"):
                full_turns.append(r.turn)                  # asked-for explanations are exempt; only unprompted restating counts
            fu = r.plan.get("followup")
            if fu:
                asked.append((r.turn, fu["id"]))
            assert r.answer.count("?") <= 4                                    # at most one question of ours (plus quoted text)
        # the full protocol is not restated within 3 turns unless the person asked how it works
        for a, b in zip(full_turns, full_turns[1:]):
            assert b - a > 1, (walk, full_turns)
        # one-time questions are not repeated endlessly
        for once in ("FU-ALLERGY", "FU-STATUS", "FU-CONSULTANT", "FU-SAFETY"):
            assert sum(1 for _, f in asked if f == once) <= 3, (walk, asked)


def test_a_thousand_answers_stay_fast_and_do_not_leak_memory(engine):
    import tracemalloc
    rng = random.Random(99)
    tracemalloc.start()
    engine.answer("warm up", None, None)
    base = tracemalloc.get_traced_memory()[0]
    started = time.time()
    for _ in range(1000):
        engine.answer(make_message(rng), None, rng.choice(PROFILES))
    elapsed = time.time() - started
    growth = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    assert elapsed < 90, f"1000 answers took {elapsed:.0f}s"
    assert growth < 40_000_000, f"memory grew by {growth / 1e6:.1f} MB over 1000 answers"


def test_engine_start_up_is_quick(settings):
    from dixit_answer.llm import MockLLM
    from dixit_answer.pipeline import Engine
    import copy
    started = time.time()
    Engine(copy.copy(settings), llm=MockLLM())
    assert time.time() - started < 10
