"""'why?' and 'tell me more' say only what is already written about the last answer, and never invent a reason."""
import random

import pytest

from dixit_answer.explain import NO_REASON, readable_source
from dixit_answer.state import ChatState
from dixit_answer.understand import EXPLAIN_FOLLOW
from tests.edge_helpers import assert_valid

PROFILE = {"allergies": ["soy"], "diet_type": "vegetarian"}


def chat(engine, *messages, profile=PROFILE):
    cid, out = None, []
    for message in messages:
        r = engine.answer(message, cid, profile)
        cid = r.conversation_id
        out.append(r)
    return out


@pytest.mark.parametrize("why", ["why", "Why?", "why so", "how come", "tell me more", "more details", "explain", "explain that", "what do you mean", "how so",
                                 "where is that from", "where does this come from", "how do you know", "the reason", "ok why"])
def test_why_after_a_recipe_gives_the_written_reason(full_engine, why):
    r = chat(full_engine, "i have paneer", why)[1]
    assert r.type == "explain_last" and "Vegetables with protein from paneer." in r.answer, (why, r.answer[:150])
    assert "calculated from the USDA values" in r.answer
    assert_valid(r, why)
    assert not r.fallback


def test_why_after_a_number_names_the_source(engine):
    r = chat(engine, "how many calories in dal", "why")[1]
    assert r.type == "explain_last" and "comes from" in r.answer and "per bowl" in r.answer
    usda = chat(engine, "how many calories in rice", "tell me more")[1]
    assert "USDA FoodData Central" in usda.answer


def test_why_after_a_programme_answer_does_not_invent_a_reason(full_engine):
    r = chat(full_engine, "can i eat rice", "why")[1]
    assert r.type == "explain_last" and r.answer.startswith(NO_REASON)
    for word in ("because", "studies", "research", "evidence", "proven"):
        assert word not in r.answer.lower()


def test_why_with_nothing_before_it_is_out_of_scope(full_engine):
    for sequence in (["why"], ["hello", "why"], ["tell me more"]):
        assert chat(full_engine, *sequence)[-1].type == "out_of_scope"


def test_the_explanation_does_not_replace_what_it_explains(full_engine):
    r = chat(full_engine, "how many calories in dal", "why", "and protein?")
    assert r[2].type == "neutral_nutrient_lookup" and "protein" in r[2].answer and "dal" in r[2].answer.lower()
    again = chat(full_engine, "i have paneer", "why", "what else")
    assert "Palak Paneer Bhurji" not in again[2].answer


def test_the_explanation_is_forgotten_after_a_few_turns(full_engine):
    r = chat(full_engine, "i have paneer", "hello", "hello", "hello", "hello", "why")[-1]
    assert r.type == "out_of_scope"


def test_a_sentence_that_only_contains_why_is_not_an_explain_request(full_engine):
    for text in ("why is paneer allowed in a cold climate with sugar tablets", "why not eat sweets", "tell me more recipes with paneer please now"):
        r = chat(full_engine, "i have paneer", text)[1]
        assert r.type != "explain_last", text


def test_every_source_has_plain_words():
    assert readable_source("USDA") == "USDA FoodData Central"
    assert "calculated" in readable_source("calculated from ingredients")
    assert "not yet signed off" in readable_source("Indian library, development only (licence pending)")


def test_the_pattern_only_matches_whole_short_messages():
    for text in ("why", "tell me more", "where is that from", "ok why", "source"):
        assert EXPLAIN_FOLLOW.match(text)
    for text in ("why is rice allowed", "tell me more about dal", "reasons", "", "whyyy", "explain the program"):
        assert not EXPLAIN_FOLLOW.match(text)


def test_an_old_state_without_the_new_fields_loads():
    state = ChatState.from_json('{"turn": 2}', "c")
    assert state.last_explain is None and state.last_explain_turn == -99
    assert ChatState.from_json('{"last_explain": "x"}', "c").last_explain is None


def test_fuzz_explain_follow_ups_always_valid(full_engine):
    rng = random.Random(4)
    openers = ["i have paneer", "calories in dal", "can i eat rice", "hello", "what else", "i have aloo"]
    follow = ["why", "tell me more", "where is that from", "what else", "and protein?", "explain", "why?", "source", "how come"]
    for _ in range(120):
        for item in chat(full_engine, rng.choice(openers), *(rng.choice(follow) for _ in range(rng.randint(1, 4)))):
            assert_valid(item)
