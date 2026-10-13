"""The checks catch broken drafts, and a bad AI draft never reaches the person."""
import copy

from dixit_answer.checks import blocking, check_ai_text
from dixit_answer.llm import LLMError
from dixit_answer.pipeline import Engine
from dixit_answer.planner import make_plan
from dixit_answer.state import ChatState
from dixit_answer.understand import understand

LIVE_DIXIT = ("I'm glad you're asking — it shows you're ready. Anything that triggers insulin secretion counts as a meal. "
              "Rice and wheat are both roughly 70% carbohydrate, so there is no meaningful difference in weight outcomes.")
GPT_STYLE = ("Evening snack: roasted chana or one fruit. Chapati: 1–2 medium chapatis. A 1200-calorie Indian diet plan with "
             "meal timings and portions. You don't have to skip dinner.")


def _draft(engine, message="i have obesity . so what should i eat to reduce it"):
    state = ChatState("x")
    u = understand(message, state, engine.index, engine.words)
    plan = make_plan(u, state)
    return engine.composer.build(plan, state, u), plan


def test_live_dixit_style_text_fails(engine):
    draft, plan = _draft(engine)
    ids = {r.check_id for r in blocking(check_ai_text(LIVE_DIXIT, draft, plan, engine.index, engine.words))}
    assert {"PH", "N15", "N20", "N21", "N19"} <= ids


def test_gpt_style_text_fails(engine):
    draft, plan = _draft(engine)
    ids = {r.check_id for r in blocking(check_ai_text(GPT_STYLE, draft, plan, engine.index, engine.words))}
    assert {"N4", "N17c", "N15"} <= ids


def test_template_passes_its_own_checks(engine):
    draft, plan = _draft(engine)
    assert not blocking(check_ai_text(draft.template(), draft, plan, engine.index, engine.words))


class ScriptedLLM:
    name, model = "scripted", "test"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def reword(self, draft_text, user_message, max_words, failed_checks=None):
        self.calls.append(failed_checks)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(draft_text) if callable(reply) else reply


def _engine_with(settings, llm):
    s = copy.copy(settings)
    return Engine(s, llm=llm)


def test_good_ai_rewording_is_used(settings):
    llm = ScriptedLLM([lambda t: t.replace("Thank you for asking.", "Thanks for reaching out.")])
    r = _engine_with(settings, llm).answer("i have obesity . so what should i eat to reduce it")
    assert r.ai_used and not r.fallback and r.answer.startswith("Thanks for reaching out.")


def test_bad_ai_draft_is_retried_then_template_is_sent(settings):
    add_number = lambda t: t + "\nEat 3 rotis."
    llm = ScriptedLLM([add_number, add_number])
    r = _engine_with(settings, llm).answer("i have obesity . so what should i eat to reduce it")
    assert r.fallback and not r.ai_used
    assert "3 rotis" not in r.answer
    assert llm.calls[1]                      # the retry was told which checks failed


def test_ai_that_adds_a_food_is_rejected(settings):
    llm = ScriptedLLM([lambda t: t + "\nTry some mango too.", lambda t: t])
    r = _engine_with(settings, llm).answer("i have chapati, what sabji can i eat?")
    assert r.ai_used and "mango" not in r.answer   # second attempt (unchanged) passes


def test_ai_error_falls_back_to_template(settings):
    r = _engine_with(settings, ScriptedLLM([LLMError("HTTP 503")])).answer("what should i eat")
    assert r.fallback and "How Dixit meals work" in r.answer
