"""Answer checks (stage 8). Run on the AI's text (before placeholders are filled) and on the final text.

Implemented now: N1/N15 numbers, N4 Golden Rules wording, N6 personal claims, N9 status for plain facts, N16 foods,
N17c eating portions, N18 caution with timing advice, N19 outcome claims, N20 jargon, N21 tone and filler praise,
N22 protocol repetition, N23 question answered, N27 instructions from the message, plus placeholder integrity and length.
Phase 5 adds N24-N26 (allergens, recipe gate, recipe steps).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .compose import PLACEHOLDER_RE, Draft
from .foods import FoodIndex
from .planner import AnswerPlan
from .state import ChatState
from .textnorm import clean, has_phrase, word_count

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    level: str
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _fail(check_id: str, level: str, detail: str) -> CheckResult:
    return CheckResult(check_id, False, level, detail)


def check_ai_text(ai_text: str, draft: Draft, plan: AnswerPlan, index: FoodIndex, words: dict) -> list[CheckResult]:
    """Checks on the part the AI wrote. Locked placeholders are removed before the word checks."""
    results: list[CheckResult] = []
    expected = PLACEHOLDER_RE.findall(draft.template())
    got = PLACEHOLDER_RE.findall(ai_text)
    if got != expected:
        missing = [p for p in expected if p not in got]
        extra = [p for p in got if p not in expected]
        detail = f"missing {missing}, extra {extra}" if (missing or extra) else "placeholders out of order or repeated"
        results.append(_fail("PH", "critical", detail))
    free_text = PLACEHOLDER_RE.sub(" ", ai_text)
    if NUMBER_RE.search(free_text):
        results.append(_fail("N15", "critical", "a number was written outside the locked items"))
    cleaned = clean(free_text, words["spelling_map"])
    template_free = clean(PLACEHOLDER_RE.sub(" ", draft.template()), words["spelling_map"])
    allowed = set(draft.foods_named) | {m.food_id for m in index.find(template_free, already_clean=True)}
    named = {m.food_id for m in index.find(cleaned, already_clean=True)}
    extra_foods = sorted(index.name(f) for f in named - allowed)
    if extra_foods:
        results.append(_fail("N16", "critical", f"names foods not in the answer plan: {extra_foods}"))
    results += _word_checks(cleaned, words)
    if word_count(draft.fill(ai_text)) > plan.max_words * 1.25:
        results.append(_fail("LEN", "major", f"longer than {plan.max_words} words"))
    return results


def _word_checks(cleaned: str, words: dict) -> list[CheckResult]:
    out = []
    for key, check_id, level in (("rule_relaxing", "N4", "critical"), ("personal_claims", "N6", "critical"),
                                 ("outcome_claims", "N19", "critical"), ("jargon", "N20", "major"),
                                 ("blame_words", "N21", "major"), ("filler_praise", "N21", "major"),
                                 ("portion_phrases", "N17c", "critical"), ("injection_markers", "N27", "critical")):
        hits = [w for w in words[key] if has_phrase(cleaned, clean(w))]
        if hits:
            out.append(_fail(check_id, level, f"{key}: {hits}"))
    if re.search(r"\b\d+\s*(?:to|-)?\s*\d*\s*(chapatis?|rotis?|bowls?|cups?|plates?|servings?|pieces?)\b(?! of)", cleaned) \
            and not has_phrase(cleaned, "per bowl"):
        out.append(_fail("N17c", "critical", "an eating amount appears"))
    return out


def check_final(final_text: str, draft: Draft, plan: AnswerPlan, state: ChatState, words: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    locked_numbers = {n for text in draft.locked.values() for n in NUMBER_RE.findall(text)}
    stray = [n for n in NUMBER_RE.findall(final_text) if n not in locked_numbers]
    if stray:
        results.append(_fail("N1", "critical", f"numbers without a record or locked text: {stray}"))
    cleaned = clean(final_text)
    timing = any(has_phrase(cleaned, clean(t)) for t in words["timing_markers"])
    if plan.protocol_level == "full" and state.risk_level() != "none" and "C1" not in draft.locked:
        results.append(_fail("N18", "critical", "meal-timing advice without the safety caution"))
    if plan.protocol_level == "full" and plan.type not in {"protocol_explain", "meal_protocol_application"} \
            and state.protocol_explained_turn is not None and state.turn + 1 - state.protocol_explained_turn <= 3:
        results.append(_fail("N22", "major", "full protocol repeated within 3 turns"))
    if plan.type in {"food_choice_guidance", "neutral_food_comparison"} and not draft.foods_named - set(plan.pairs_with):
        results.append(_fail("N23", "major", "a which/what question got no named food"))
    if plan.type in {"neutral_nutrient_lookup", "neutral_food_comparison", "source_fact_lookup"} and \
            plan.followup and plan.followup["slot"] == "safety":
        results.append(_fail("N9", "major", "asked about health status for a plain fact"))
    if timing and plan.protocol_level == "none" and plan.type not in {"followup_reply"} and not plan.reply_slot:
        if any(has_phrase(cleaned, clean(t)) for t in ("two meals", "2 meals", "nothing in between")):
            results.append(_fail("N22", "major", "protocol text appeared although the plan said none"))
    results += _word_checks(clean(PLACEHOLDER_RE.sub(" ", draft.template())), words)   # our own template must pass too
    return results


def blocking(results: list[CheckResult]) -> list[CheckResult]:
    """A draft fails on any critical result, or two or more major ones."""
    failed = [r for r in results if not r.passed]
    critical = [r for r in failed if r.level == "critical"]
    major = [r for r in failed if r.level == "major"]
    return failed if (critical or len(major) >= 2 or any(r.check_id in {"PH", "LEN", "N22", "N23"} for r in failed)) else []
