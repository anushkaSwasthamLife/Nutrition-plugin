"""Reading a diabetes status from what a person says (NUT-STATUS-001: asked only when a rule depends on it).

Statuses: diabetic, prediabetic, non_diabetic, currently_non_diabetic (had it, readings now normal). For the last one the
programme also needs to know whether a consultant has validated it; until then the restriction continues.
"""
from __future__ import annotations

import re

from .golden import Status
from .textnorm import clean

NEG_DIABETES = re.compile(r"\b(non diabetic|not diabetic|no diabetes|never (had|been) diabet\w*|(do not|dont) have diabetes|"
                          r"not a diabetic|i am not diabetic|im not diabetic|no sugar problem)\b")
NOW_NORMAL = re.compile(r"\b(now non diabetic|no longer diabetic|not diabetic anymore|not diabetic any more|used to be diabetic|"
                        r"was diabetic|had diabetes|had diabetic|diabetes (is |has )?(now )?(normal|gone|reversed|cured|controlled)|"
                        r"reversed (my )?diabetes|diabetes reversal|sugar (is )?(now )?normal|sugar levels? (are )?(now )?normal|"
                        r"currently non diabetic)\b")
PRE = re.compile(r"\b(pre diabet\w*|prediabet\w*|borderline (sugar|diabet\w*)|high sugar but not diabet\w*)\b")
DIABETIC = re.compile(r"\b(diabet\w*|type 2|type two|type ii|sugar patient|sugar problem|high sugar|sugar is high)\b")
VALIDATED = re.compile(r"\b((consultant|doctor|physician|dixit coach|coach) (has |have )?(confirmed|validated|cleared|verified|certified)|"
                       r"(confirmed|validated|cleared|verified|certified) by (my |the )?(consultant|doctor|physician|coach)|"
                       r"consultant validated)\b")
NOT_VALIDATED = re.compile(r"\b(not (yet )?(been )?(confirmed|validated|cleared|verified)|no consultant|"
                           r"(consultant|doctor) (has )?(not|hasnt|hasnt yet|did not|didnt) (confirmed|validated|cleared))\b")


FIRST_PERSON = re.compile(r"\b(i|im|ive|id|my|me|myself|mine|we|our)\b")


def parse_status(text: str, reply: bool = False) -> dict:
    """Return any of {"status", "prior_history", "consultant_validated"} the text states; {} if it states nothing.

    Outside a reply to our status question the text must be about the speaker ("I have diabetes"); "can someone with diabetes
    eat mango?" says nothing about the person asking."""
    cleaned = clean(text).replace("'", "")
    if not reply and not FIRST_PERSON.search(cleaned):
        return {}
    found: dict = {}
    if NOW_NORMAL.search(cleaned):
        found.update(status="currently_non_diabetic", prior_history=True)
    elif NEG_DIABETES.search(cleaned):
        found.update(status="non_diabetic", prior_history=False)
    elif PRE.search(cleaned):
        found.update(status="prediabetic")
    elif DIABETIC.search(cleaned):
        found.update(status="diabetic")
    if NOT_VALIDATED.search(cleaned):
        found["consultant_validated"] = False
    elif VALIDATED.search(cleaned):
        found["consultant_validated"] = True
    return found


def status_from_state(state) -> Status:
    return Status(state.diabetes_status, state.prior_diabetes_history, state.consultant_validated)


def apply_status(state, found: dict) -> bool:
    """Record what was stated. Returns True if anything changed."""
    changed = False
    if "status" in found and state.diabetes_status != found["status"]:
        state.diabetes_status, changed = found["status"], True
        if found["status"] != "currently_non_diabetic":
            state.consultant_validated = None          # only meaningful after "now normal"
    if "prior_history" in found:
        state.prior_diabetes_history, changed = found["prior_history"], True
    if "consultant_validated" in found:
        state.consultant_validated, changed = found["consultant_validated"], True
    return changed
