"""The AI must not glue words in front of a locked sentence ("Sure, Here are...", "Let me know Which one should I show?").

Found 16 Oct 2026 with the live model on "I have obesity, what sabji would you recommend?".
"""
import copy
import re

import pytest

from dixit_answer.checks import _glued_before_placeholder, blocking, check_ai_text
from dixit_answer.pipeline import Engine
from test_checks_and_ai import ScriptedLLM

ASK = "i have obesity , so i need a healthy meal . what sabji would u recommend"


@pytest.mark.parametrize("text", [
    "Sure, {{T1}}", "Let me know {{T2}}", "Try {{T3}}", "Hope {{K1}}", "Consider {{T1}}", "ok {{T1}}", "- Sure {{T1}}",
    "Intro sentence. Sure, {{T1}}", "**Tip:** Try {{T1}}", "Yes,{{T1}}", "Here you go {{T1}}"])
def test_words_glued_in_front_of_a_locked_sentence_are_found(text):
    assert _glued_before_placeholder(text), text


@pytest.mark.parametrize("text", [
    "{{T1}}", "- {{K1}}", "**Whole dish:** {{F1}}", "**Tip:** {{T1}}", "Thank you for asking. {{T1}}", "Here you go: {{T1}}",
    "Great!\n{{T1}}", "What a day? {{T1}}", "- {{K1}}\n- {{K2}}", "", "no placeholder here", "{{T1}} {{T2}}",
    "Fill-up sabjis: {{T1}} and {{T2}}" if False else "Fill-up sabjis: {{T1}}", "1. {{T1}}" if False else "- {{T1}}"])
def test_properly_placed_locked_sentences_are_not_flagged(text):
    assert _glued_before_placeholder(text) == [], text


def test_the_flag_is_per_line():
    assert _glued_before_placeholder("Hello there.\n{{T1}}\nOK so {{T2}}") == ["OK so"]


def test_a_template_never_trips_its_own_check(engine):
    for q in [ASK, "I have lauki and tomato, what can I cook?", "how do I make palak paneer?", "What sabji can I eat?",
              "How many carbs in one chapati?", "Can I snack between meals?", "I feel very hungry between meals",
              "i have obesity . so what should i eat to reduce it"]:
        r = engine.answer(q, None, {"allergies": []})
        assert r.fallback is False
    from dixit_answer.planner import make_plan
    from dixit_answer.state import ChatState
    from dixit_answer.understand import understand
    state = ChatState("x")
    u = understand(ASK, state, engine.index, engine.words)
    draft = engine.composer.build(make_plan(u, state), state, u)
    assert _glued_before_placeholder(draft.template()) == []


def test_glued_text_is_retried_with_the_reason_then_template_is_sent(settings):
    glue = lambda t: re.sub(r"(\{\{T\d+\}\})", r"Sure, \1", t, count=1)
    llm = ScriptedLLM([glue, glue])
    r = Engine(copy.copy(settings), llm=llm).answer(ASK, None, {"allergies": []})
    assert r.fallback and not r.ai_used and "Sure," not in r.answer
    assert llm.calls[1] and any("JOIN" in c for c in llm.calls[1])


def test_the_retry_can_fix_it(settings):
    glue = lambda t: re.sub(r"(\{\{T\d+\}\})", r"Sure, \1", t, count=1)
    llm = ScriptedLLM([glue, lambda t: t])
    r = Engine(copy.copy(settings), llm=llm).answer(ASK, None, {"allergies": []})
    assert r.ai_used and not r.fallback and "Sure," not in r.answer


def test_a_clean_reword_with_its_own_sentence_still_passes(settings):
    llm = ScriptedLLM([lambda t: "Happy to help.\n" + t])
    r = Engine(copy.copy(settings), llm=llm).answer(ASK, None, {"allergies": []})
    assert r.ai_used and r.answer.startswith("Happy to help.")


def test_the_check_is_blocking_on_its_own(engine):
    from dixit_answer.checks import CheckResult
    assert blocking([CheckResult("JOIN", False, "major", "x")])
