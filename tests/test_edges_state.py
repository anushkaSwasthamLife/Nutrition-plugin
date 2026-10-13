"""Edge cases for conversations: ids, long chats, corrupted or old state, stale replies, concurrency."""
import json
import sqlite3
import threading

import pytest

from edge_helpers import assert_valid

PROFILE = {"allergies": []}


@pytest.mark.parametrize("cid", ["x" * 500, "../../etc/passwd", "a'; DROP TABLE chat_state;--", "नमस्ते", "😀", " ", "with space",
                                 "0", "null", "{{F1}}", "a" * 64])
def test_odd_conversation_ids_work_and_stay_separate(engine, cid):
    r1 = engine.answer("I have aloo and onion, what can I make?", cid, PROFILE)
    r2 = engine.answer("aloo", cid, PROFILE)
    assert r1.conversation_id == cid and r2.turn == r1.turn + 1
    assert_valid(r1), assert_valid(r2)
    engine.reset(cid)
    assert engine.answer("hello", cid, PROFILE).turn == 1


def test_forty_turn_conversation_keeps_invariants(engine):
    messages = ["i have obesity what should i eat", "no medicines", "I have chapati what sabji", "bhindi aloo", "aloo", "give me recipes",
                "aloo gobhi", "none", "I have aloo and onion what can I make", "can I have mango?", "no", "thank you",
                "I feel hungry between meals", "recipe for chicken curry", "palak", "how does the program work", "can I snack?"] * 3
    cid, full_turns = None, []
    for i, m in enumerate(messages[:40]):
        r = engine.answer(m, cid, None)
        cid = r.conversation_id
        assert_valid(r, f"turn {i}: {m}")
        if "How Dixit meals work" in r.answer:
            full_turns.append(r.turn)
    assert r.turn == 40
    state = engine.store.load(cid)
    assert len(state.facts_given) == len(set(state.facts_given))             # no duplicate facts recorded
    assert len(state.home_foods) == len(set(state.home_foods))


def test_corrupted_state_row_does_not_crash_the_chat(engine):
    cid = "corrupt-state-1"
    engine.answer("hello", cid, PROFILE)
    db = sqlite3.connect(str(engine.settings.state_db))
    db.execute("UPDATE chat_state SET state = ? WHERE conversation_id = ?", ("{this is not json", cid))
    db.commit(), db.close()
    r = engine.answer("I have aloo and onion, what can I make?", cid, PROFILE)
    assert_valid(r)
    assert "Aloo Pyaz" in r.answer


@pytest.mark.parametrize("payload", ['{"conversation_id": "old"}', '{"conversation_id": "old", "turn": 3, "unknown_future_field": 1}',
                                     '{"conversation_id": "old", "safety_status": {}}', '[]', '"just a string"', "null", ""])
def test_old_or_malformed_state_is_tolerated(engine, payload):
    cid = f"old-state-{abs(hash(payload)) % 10000}"
    engine.answer("hello", cid, PROFILE)
    db = sqlite3.connect(str(engine.settings.state_db))
    db.execute("UPDATE chat_state SET state = ? WHERE conversation_id = ?", (payload, cid))
    db.commit(), db.close()
    assert_valid(engine.answer("can I have bhindi?", cid, PROFILE))


def test_a_stale_reply_is_not_read_as_an_answer(engine):
    r1 = engine.answer("what sabji can I eat with chapati", None, PROFILE)          # asks which sabjis are cooked at home
    engine.answer("how many carbs in one idli?", r1.conversation_id, PROFILE)        # moves on
    r3 = engine.answer("bhindi", r1.conversation_id, PROFILE)
    assert r3.plan["reply_slot"] == ""                                                # no pending question any more
    assert_valid(r3)


def test_a_reply_with_no_question_pending_is_just_a_new_message(engine):
    for text in ("none", "yes", "no", "milk", "vegetarian", "aloo gobhi", "bhindi, aloo"):
        assert_valid(engine.answer(text, None, PROFILE), text)


def test_a_new_question_instead_of_the_answer_is_answered_as_a_question(engine):
    r1 = engine.answer("I have aloo and onion, what can I make?", None, None)        # asks about allergies
    r2 = engine.answer("how many carbs in one chapati?", r1.conversation_id, None)
    assert r2.type == "neutral_nutrient_lookup" and "46.1 g carbohydrate" in r2.answer


def test_safety_status_is_never_lost_between_turns(engine):
    r1 = engine.answer("I take insulin", None, PROFILE)
    r2 = engine.answer("how does the program work?", r1.conversation_id, PROFILE)
    assert "Because you take medicine for diabetes" in r2.answer
    r3 = engine.answer("can I snack between meals?", r1.conversation_id, PROFILE)
    assert "please agree any change" in r3.answer


def test_emergency_is_recognised_mid_conversation_and_after_noise(engine):
    r1 = engine.answer("I have aloo and onion", None, PROFILE)
    r2 = engine.answer("sorry, now I'm shaking and sweating and very dizzy, took insulin", r1.conversation_id, PROFILE)
    assert r2.type == "emergency" and "112" in r2.answer


# ---------------------------------------------------------------- concurrency
def test_many_conversations_at_once_do_not_mix_or_crash(engine):
    errors, results = [], {}

    def worker(n):
        try:
            cid = f"conc-{n}"
            profile = {"allergies": ["milk"] if n % 2 else []}
            seen = []
            for m in ("I have bhindi and onion, what can I make?", "how do I make moong dal tadka?", "can I have paneer?",
                      "I have aloo and onion, what can I make?", "give me recipes", "aloo gobhi"):
                r = engine.answer(m, cid, profile)
                assert_valid(r, f"{cid}: {m}")
                seen.append(r)
            results[n] = seen
        except Exception as exc:                                               # noqa: BLE001 - collected and asserted below
            errors.append((n, repr(exc)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    for n, seen in results.items():
        if n % 2:                                                               # milk allergy: never ghee / paneer / curd offered
            for r in seen:
                body = r.answer
                assert "ghee: " not in body and "paneer: " not in body and "curd: " not in body, (n, body[:300])


def test_many_turns_on_one_conversation_from_several_threads_never_crash(engine):
    errors = []

    def worker():
        try:
            for m in ("hello", "I have aloo and onion, what can I make?", "none", "can I have mango?", "thank you"):
                assert_valid(engine.answer(m, "shared-conv", PROFILE))
        except Exception as exc:                                               # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert engine.store.load("shared-conv").turn > 0
