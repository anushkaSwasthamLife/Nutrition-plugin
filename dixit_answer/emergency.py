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
    r"(?<![\w])(?:blood\s+)?(?:sugar|glucose|bg|rbs|fbs|ppbs|grbs|\u0936\u0941\u0917\u0930|\u0917\u094d\u0932\u0942\u0915\u094b\u095b|\u0917\u094d\u0932\u0942\u0915\u094b\u091c)(?![\w])"
    r"(?:\s+(?:level|levels|reading|readings|is|was|at|of|came|comes|now|just|only|showing|shows|around|about|went|has|have|"
    r"dropped|fell|gone|down|up|to|ho|gayi|gaya|hai|aa|rahi|raha|\u0932\u0947\u0935\u0932|\u0939\u0948|\u0939\u094b|\u0917\u0908|\u0917\u092f\u093e|\u0905\u092d\u0940|\u0906\u091c))*"
    r"\s*[:=-]?\s*(\d{1,3}(?:\.\d+)?)\s*(mg\s*/?\s*dl|mg|mmol(?:\s*/\s*l)?)?",
)
_CLAUSE_SPLIT = re.compile(r"[.,;:!?\u0964]+|(?<![\w])(?:but|however|although|lekin|magar)(?![\w])", re.IGNORECASE)   # \u0964 is the Hindi full stop
_DEVANAGARI_DIGITS = str.maketrans("\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f", "0123456789")
_FOOD_UNIT_RE = re.compile(r"^\s*(?:g|gm|gms|gram|grams|kg|ml|l|litre|liter|tsp|tbsp|spoons?|teaspoons?|tablespoons?|cups?|"
                           r"kcal|cal|calories|%|percent|pieces?)(?![\w])")


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
        self._patterns: dict = {}
        self.window = int(rules.get("negation_window_words", 3))
        self.negated_note = rules.get("negated_note", "")
        self.glucose = rules.get("glucose")                # None: no blood-sugar readings (the harmful-intent rules)
        self.categories = sorted(rules["categories"], key=lambda c: c["priority"])
        self.by_id = {c["id"]: c for c in self.categories}
        info = rules.get("informational", {})
        self.definition_starts = [clean(x) for x in info.get("definition_starts", [])]
        self.food_words = {clean(x) for x in info.get("food_words", [])}
        self.urgent_words = {clean(x) for x in info.get("urgent_words", [])}
        self.fillers = {clean(x) for x in rules.get("negation_fillers", [])}
        def alts(key: str) -> str:                         # an empty list must match nothing, never everything
            return "|".join(re.escape(clean(x)) for x in (info.get(key) or ["\0"]))
        self.past_re = re.compile(r"(?<![\w])(?:" + alts("past_markers") + r")(?![\w])")
        art = r"(?: (?:a|an|the|my|his|her|your))?"
        self.before_re = re.compile(r"(?<![\w])(?:" + alts("before_markers") + r")" + art + r"$")
        self.after_re = re.compile(r"^ (?:" + alts("after_markers") + r")(?![\w])")
        self.hazard_re = re.compile(r"^ (?:" + alts("hazard_markers") + r")(?![\w])")
        for cat in self.categories:
            cat["_phrases"] = [clean(p) for p in cat["phrases"]]
            cat["_combos"] = [[[clean(w) for w in group] for group in combo] for combo in cat.get("combos", [])]
        self.symptom_words = {w for c in self.categories for p in c["_phrases"] for w in p.split()} | \
            {w for c in self.categories for combo in c["_combos"] for group in combo for x in group for w in x.split()}

    # ------------------------------------------------------------------
    def _negated_before(self, tokens: list[str], negations) -> bool:
        """A negation counts only when it sits right in front of the symptom: nothing but small filler words, or other symptoms in a
        list ("without fainting or chest pain"), may stand between them. "without help I can't breathe" is not negated."""
        single = {n for n in negations if " " not in n}
        multi = [n.split() for n in negations if " " in n]
        joined = False                                    # have we passed "or" / "and", so earlier symptoms belong to the same list?
        for steps, i in enumerate(range(len(tokens) - 1, -1, -1)):
            if steps >= 6:
                return False
            tok = tokens[i]
            if tok in single or any(tokens[max(0, i - len(m) + 1):i + 1] == m for m in multi):
                return True
            if tok in ("or", "and"):
                joined = True
            elif tok in self.symptom_words and joined:
                continue                              # "without fainting or chest pain": fainting belongs to the same negated list
            elif tok not in self.fillers:
                return False                          # "no chest pain I fainted": a symptom with no "or" in between starts a new statement
        return False

    def _informational(self, clause: str, start: int, end: int) -> bool:
        """The symptom is named, but nobody is describing something happening now: a definition ("what is anaphylaxis"), a long-past
        event ("my father had a heart attack last year"), or a diet question about it ("what to eat after a stroke")."""
        before, after = clause[:start].strip(), clause[end:]
        if set(clause.split()) & self.urgent_words:         # "now", "still", "again", "help" ...: it is happening, whatever else the sentence says
            return False
        if any(before in (s, f"{s} a", f"{s} an", f"{s} the") for s in self.definition_starts):
            return True
        if self.past_re.search(clause):
            return True
        if set(clause.split()) & self.food_words:
            if self.before_re.search(before) or self.after_re.search(after):
                return True
        return bool(self.hazard_re.search(after))

    def _scan(self, clauses: list[str], phrase: str, negations, negatable: bool = True) -> tuple[bool, bool]:
        """(found and counts, found but set aside). Set aside means negated ("no chest pain") or only talked about (see _informational).
        Both are judged inside one clause (clauses end at . , ; : ! ? and at "but"). A category that is not negatable (self-harm)
        is never set aside."""
        positive = set_aside = False
        pattern = self._pattern(phrase)
        for clause in clauses:
            for match in pattern.finditer(clause):
                aside = negatable and (self._negated_before(clause[:match.start()].split(), negations)
                                       or self._informational(clause, match.start(), match.end()))
                positive |= not aside
                set_aside |= aside
        return positive, set_aside

    def _pattern(self, phrase: str):
        """The whole-word pattern for a phrase, compiled once. (Compiling it on every message overflowed Python's 512-pattern cache once
        the emergency and harmful-intent lists together passed 450 phrases, and slowed every answer.)"""
        pattern = self._patterns.get(phrase)
        if pattern is None:
            pattern = self._patterns[phrase] = re.compile(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])")
        return pattern

    def reading(self, text: str) -> float | None:
        """The lowest or highest blood-sugar reading in the message, in mg/dL (the one furthest from normal)."""
        values = []
        lowered = (text or "").lower().translate(_DEVANAGARI_DIGITS)
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
        text = text if isinstance(text, str) else ""
        clauses = [c for c in (clean(part) for part in _CLAUSE_SPLIT.split(text)) if c]
        out = EmergencyResult()
        for cat in self.categories:
            negatable = cat.get("negatable", True)
            for phrase in cat["_phrases"]:
                positive, negated = self._scan(clauses, phrase, negations, negatable)
                if positive:
                    out.matched.append({"category": cat["id"], "how": "phrase", "what": phrase})
                elif negated:
                    out.negated.append(phrase)
            for combo in cat["_combos"]:
                hits = []
                for group in combo:
                    hit = next((w for w in group if self._scan(clauses, w, negations, negatable)[0]), None)
                    if hit is None:
                        break
                    hits.append(hit)
                else:
                    out.matched.append({"category": cat["id"], "how": "combo", "what": " + ".join(hits)})
        value = self.reading(text) if self.glucose else None
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
