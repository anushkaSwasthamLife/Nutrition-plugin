"""Safety gate (stage 3).

1. Emergency check (`emergency.py`, rules in `data/emergency_rules.json`): a match ends the turn with that category's fixed
   reply, before anything else and without the AI. Dixit's own emergency rules will run before ours once the base package is here.
2. Risk words update the safety status used by the pre-check (NUT-SAFE-PRE-001): insulin or sugar tablets, pregnancy,
   kidney disease, eating disorder, under 18. A risk word after a negation ("no insulin", "not pregnant") sets "no".
3. Harmful-intent check (`harmful.py`, guardrail A3), only when there is no emergency: starving, purging, diet pills or an
   unsafe speed of weight loss get that category's fixed reply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .emergency import EmergencyResult, EmergencyRules
from .state import SAFETY_FIELDS, ChatState
from .textnorm import clean, has_phrase

_DEFAULT_RULES: EmergencyRules | None = None


def default_emergency_rules() -> EmergencyRules:
    global _DEFAULT_RULES
    if _DEFAULT_RULES is None:
        import json
        from .config import DATA_DIR
        _DEFAULT_RULES = EmergencyRules(json.loads((DATA_DIR / "emergency_rules.json").read_text(encoding="utf-8")))
    return _DEFAULT_RULES


# Used only if a category has no reply of its own.
EMERGENCY_ANSWER = ("This sounds like it could be an emergency. If you feel shaky, sweaty, confused or faint, or have chest pain "
                    "or trouble breathing, please get medical help now: call your doctor or emergency services (112 in India). "
                    "If you take insulin or sugar tablets and feel shaky, follow the low-sugar steps your doctor gave you.")

_AGE_RE = re.compile(r"\b(?:i am|i'm|im|age|aged)\s+(\d{1,2})\b|\b(\d{1,2})\s*(?:years? old|yrs? old|yo)\b")
# An age that follows "my ..." is someone else's ("my 5 year old", "my son is 8 years old"): it never changes the person's own status.
_SOMEONE_ELSES = re.compile(r"\b(?:my|our|his|her|mera|meri|mere)\s+(?:\S+\s+){0,3}$")


@dataclass
class SafetyResult:
    emergency: bool = False
    emergency_phrase: str = ""
    em: EmergencyResult = field(default_factory=EmergencyResult)
    harm: EmergencyResult = field(default_factory=EmergencyResult)      # guardrail A3 (H1-H4); empty when it did not run
    updates: dict = field(default_factory=dict)      # safety field -> "yes" / "no"


def _negated(cleaned: str, phrase: str, negations) -> bool:
    match = re.search(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", cleaned)
    if not match:
        return False
    before = cleaned[:match.start()].split()[-4:]
    return any(n in before for n in negations if " " not in n) or any(
        has_phrase(" ".join(before), n) for n in negations if " " in n)


def check(text: str, state: ChatState, word_lists: dict, emergency_rules: EmergencyRules | None = None,
          harmful_rules: EmergencyRules | None = None) -> SafetyResult:
    cleaned = clean(text)
    result = SafetyResult()
    negations = word_lists["negation_words"]
    result.em = (emergency_rules or default_emergency_rules()).check(text, negations)
    if result.em.is_emergency:
        result.emergency, result.emergency_phrase = True, result.em.matched[0]["what"]     # (the risk words below are still read)
    elif harmful_rules is not None:
        result.harm = harmful_rules.check(text, negations)                                # an emergency always wins

    for field_name, terms in word_lists["risk_terms"].items():
        for term in terms:
            term_c = clean(term)
            if has_phrase(cleaned, term_c):
                result.updates[field_name] = "no" if _negated(cleaned, term_c, negations) else "yes"
                break

    age = next((m for m in _AGE_RE.finditer(cleaned) if not _SOMEONE_ELSES.search(cleaned[:m.start()])), None)
    if age:
        years = int(age.group(1) or age.group(2))
        if 5 <= years <= 99:
            result.updates["under_18"] = "yes" if years < 18 else "no"
    return result


def apply_safety_reply(text: str, state: ChatState, word_lists: dict) -> dict:
    """Read a reply to our safety question ("Do you take medicine for diabetes or BP, or are you pregnant?")."""
    cleaned = clean(text)
    updates = check(text, state, word_lists).updates
    said_no = any(has_phrase(cleaned, clean(w)) for w in word_lists["no_words"])
    said_yes = any(has_phrase(cleaned, clean(w)) for w in word_lists["yes_words"])
    if said_no and not any(v == "yes" for v in updates.values()):
        for key in ("diabetes_medicine", "bp_medicine", "pregnancy"):
            updates.setdefault(key, "no")
    elif said_yes and not updates:
        updates["diabetes_medicine"] = "yes"   # unspecified "yes" to a medicine question: treat as at risk
    return updates


def safety_caution(state: ChatState) -> str | None:
    """The caution placed before meal-timing advice (R2/R3), or None when risk is none."""
    level = state.risk_level()
    if level == "none":
        return None
    if level == "known":
        reasons = {
            "diabetes_medicine": "you take medicine for diabetes",
            "bp_medicine": "you take blood-pressure medicine",
            "pregnancy": "you're pregnant or breastfeeding",
            "kidney_disease": "you have kidney disease",
            "eating_disorder": "of your history with eating",
            "under_18": "you're under 18",
        }
        why = " and ".join(reasons[r] for r in state.risk_reasons()[:2])
        return (f"Because {why}, please agree any change in when or how often you eat with your doctor first. "
                "The programme below is for information, not something to start now.")
    return ("If you take medicine for diabetes or blood pressure, are pregnant, or have kidney disease, talk to your doctor "
            "before changing when you eat.")


__all__ = ["SafetyResult", "check", "apply_safety_reply", "safety_caution", "EMERGENCY_ANSWER", "SAFETY_FIELDS", "default_emergency_rules"]
