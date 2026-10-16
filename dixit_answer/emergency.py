"""Emergency check (step 0 of every turn, inside the safety gate).

Runs before anything else and never calls the AI. Rules come from `data/emergency_rules.json` (doctor to sign):

1. Phrases per category (E1-E7), matched as whole words. A phrase with a negation just before it ("no chest pain") does not
   stop the turn; the answer gets one short line instead ("If that changes ... call 112").
2. Combinations: two symptom groups in the same message ("shaky" + "sweating", "pregnant" + "bleeding").
3. Blood-sugar readings ("my sugar is 52", "glucose 3.1 mmol"): below the low line -> E1, at or above the high line -> E4.
   A number followed by g, ml, spoons, kcal ... is a food amount, not a reading.

The first category by priority wins; every match is kept for the audit log.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textnorm import clean, has_phrase

_GLUCOSE_RE = re.compile(
    r"\b(?:blood\s+)?(?:sugar|glucose|bg|rbs|fbs|ppbs|grbs)\b"
    r"(?:\s+(?:level|levels|reading|readings|is|was|at|of|came|comes|now|just|only|showing|shows|around|about|went|has|have|"
    r"dropped|fell|gone|down|up|to|ho|gayi|gaya|hai|aa|rahi|raha))*"
    r"\s*[:=-]?\s*(\d{1,3}(?:\.\d+)?)\s*(mg\s*/?\s*dl|mg|mmol(?:\s*/\s*l)?)?",
)
_FOOD_UNIT_RE = re.compile(r"^\s*(?:g|gm|gms|gram|grams|kg|ml|l|litre|liter|tsp|tbsp|spoons?|teaspoons?|tablespoons?|cups?|"
                           r"kcal|cal|calories|%|percent|pieces?)\b")


@dataclass
class EmergencyResult:
    category: str = ""                                   # "" = no emergency
    name: str = ""
    reply: str = ""
    checkin: str = ""
    matched: list = field(default_factory=list)          # [{"category", "how", "what"}]
    negated: list = field(default_factory=list)          # phrases mentioned with a negation
    glucose_mg_dl: float | None = None

    @property
    def is_emergency(self) -> bool:
        return bool(self.category)


class EmergencyRules:
    def __init__(self, rules: dict):
        self.version = rules["version"]
        self.window = int(rules.get("negation_window_words", 3))
        self.negated_note = rules["negated_note"]
        self.glucose = rules["glucose"]
        self.categories = sorted(rules["categories"], key=lambda c: c["priority"])
        self.by_id = {c["id"]: c for c in self.categories}
        for cat in self.categories:
            cat["_phrases"] = [clean(p) for p in cat["phrases"]]
            cat["_combos"] = [[[clean(w) for w in group] for group in combo] for combo in cat.get("combos", [])]

    # ------------------------------------------------------------------
    def _negated(self, cleaned: str, phrase: str, negations) -> bool:
        """True only if EVERY occurrence of the phrase has a negation in the few words just before it."""
        found = False
        for match in re.finditer(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", cleaned):
            found = True
            before = cleaned[:match.start()].split()[-self.window:]
            joined = " ".join(before)
            if not (any(n in before for n in negations if " " not in n) or
                    any(has_phrase(joined, n) for n in negations if " " in n)):
                return False
        return found

    def reading(self, text: str) -> float | None:
        """The lowest or highest blood-sugar reading in the message, in mg/dL (the one furthest from normal)."""
        values = []
        lowered = (text or "").lower()
        for m in _GLUCOSE_RE.finditer(lowered):
            if _FOOD_UNIT_RE.match(lowered[m.end():]) and not m.group(2):
                continue
            value, unit = float(m.group(1)), (m.group(2) or "").replace(" ", "")
            if unit.startswith("mmol") or (not unit and "." in m.group(1) and value <= 33):
                value = round(value * 18, 1)
            elif value < 20:
                continue                                  # "sugar 2" is spoons, not a reading
            if 20 <= value <= 900:
                values.append(value)
        if not values:
            return None
        return min(values, key=lambda v: -abs(v - 120))

    def check(self, text: str, negations) -> EmergencyResult:
        cleaned = clean(text)
        out = EmergencyResult()
        for cat in self.categories:
            for phrase in cat["_phrases"]:
                if has_phrase(cleaned, phrase):
                    if self._negated(cleaned, phrase, negations):
                        out.negated.append(phrase)
                    else:
                        out.matched.append({"category": cat["id"], "how": "phrase", "what": phrase})
            for combo in cat["_combos"]:
                hits = []
                for group in combo:
                    hit = next((w for w in group if has_phrase(cleaned, w) and not self._negated(cleaned, w, negations)), None)
                    if hit is None:
                        break
                    hits.append(hit)
                else:
                    out.matched.append({"category": cat["id"], "how": "combo", "what": " + ".join(hits)})
        value = self.reading(text)
        if value is not None:
            out.glucose_mg_dl = value
            if value < self.glucose["low_mg_dl"]:
                out.matched.append({"category": self.glucose["low_category"], "how": "reading", "what": f"{value:g} mg/dL"})
            elif value >= self.glucose["high_mg_dl"]:
                out.matched.append({"category": self.glucose["high_category"], "how": "reading", "what": f"{value:g} mg/dL"})
        if out.matched:
            hit_ids = {m["category"] for m in out.matched}
            cat = next(c for c in self.categories if c["id"] in hit_ids)
            out.category, out.name, out.reply, out.checkin = cat["id"], cat["name"], cat["reply"], cat["checkin"]
        return out


__all__ = ["EmergencyRules", "EmergencyResult"]
