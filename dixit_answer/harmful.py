"""Harmful-intent check (guardrail A3), right after the emergency check and inside the safety gate.

Requests to purge (H1), starve (H2), use diet pills (H3) or lose weight at an unsafe speed (H4) get that category's fixed reply,
with no food, no question and no AI. Rules come from `data/harmful_intent_rules.json` (doctor and mental-health reviewer to sign).
It reuses the emergency matcher, so phrases, combinations, negation ("how to lose weight without starving") and definitions
("what are diet pills") work the same way, plus:

1. Open phrases ("stop eating") count only when no food follows: "stop eating sugar to lose weight" is a fair question.
2. Speed: "lose 10 kg in a week" above the kg-per-week line -> H4. A goal with no time is never flagged.
3. Daily calories: "eat 500 calories a day" below the line -> H4, unless the number is a deficit or exercise ("burn 500").

An emergency always wins; the gate runs this only when the emergency check found nothing.
"""
from __future__ import annotations

import re

from .emergency import EmergencyResult, EmergencyRules
from .textnorm import clean

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_COUNT = {"a": 1, "an": 1, "one": 1, "ek": 1, "two": 2, "do": 2, "three": 3, "teen": 3, "four": 4, "char": 4, "chaar": 4}
_KG_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
             "twenty": 20, "paanch": 5, "panch": 5, "das": 10, "pandrah": 15, "bees": 20}
_WEEKS = {"day": 1 / 7, "days": 1 / 7, "din": 1 / 7, "dino": 1 / 7, "dinon": 1 / 7, "दिन": 1 / 7,
          "week": 1, "weeks": 1, "hafte": 1, "hafta": 1, "hafton": 1, "haphte": 1, "haphta": 1, "हफ्ते": 1,
          "month": 4.345, "months": 4.345, "mahine": 4.345, "mahina": 4.345, "mahino": 4.345, "महीने": 4.345}
_KG = r"(?P<kg>\d+(?:\.\d+)?|" + "|".join(_KG_WORDS) + r")\s*(?:kg|kgs|kilo|kilos|kilogram|kilograms|किलो)(?![\w])"
_TIME = (r"(?:(?P<n>\d+(?:\.\d+)?|" + "|".join(_COUNT) + r")\s+)?(?P<u>" + "|".join(sorted(_WEEKS, key=len, reverse=True)) + r")(?![\w])")
_IN = r"(?:in|within|under|me|mein|main|में)"
_GAP = r"(?:\s+\S+){0,3}?\s+"                 # up to three words between ("10 kg weight in", "lose 10 kg of fat in")
_SPEEDS = [re.compile(p) for p in (
    r"(?<![\w.])" + _KG + _GAP + _IN + r"\s+(?:just\s+|only\s+|sirf\s+|bas\s+)?" + _TIME,   # "10 kg in a week"
    r"(?<![\w.])" + _KG + _GAP + _TIME + r"\s+" + _IN + r"(?![\w])",                        # "10 kg 1 hafte me"
    r"(?<![\w])" + _IN + r"\s+(?:just\s+|only\s+|sirf\s+|bas\s+)?" + _TIME + _GAP + _KG,   # "in 1 week lose 10 kg"
    r"(?<![\w.])" + _TIME + r"\s+" + _IN + _GAP + _KG,                                        # "1 mahine me 10 kg kam"
)]
_KCAL_DAY = re.compile(r"(?<![\w.])(\d{1,4})\s*(?:kcal|calories|calorie|cals|cal)\s+(?:a|per|each|every|in a|ek|har|daily)?\s*"
                       r"(?:day|din|daily)(?![\w])")
_NOT_INTAKE = re.compile(r"(?<![\w])(?:burn|burns|burned|burning|deficit|less|fewer|reduce|reducing|cut|cutting|kam|extra|more|"
                         r"gain|gaining|by)(?:\s+\S+){0,2}\s*$")         # within the two words before the number: "burn 500", "cut by 500"
_CLAUSES = re.compile(r"(?<!\d)\.|\.(?!\d)|[;!?।]+")          # "1.5 kg" stays whole


def _clauses(text: str) -> list[str]:
    """Sentences, lower-cased, with digits made Western and punctuation removed, but "1.5" and Hindi letters kept whole
    (textnorm.clean would turn "1.5" into "1 5")."""
    out = []
    for clause in _CLAUSES.split((text or "").lower().translate(_DEVANAGARI_DIGITS)):
        clause = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", re.sub(r"[^\w\s.ऀ-ॿ]", " ", clause))
        out.append(" ".join(clause.split()))
    return out


def _number(word: str | None) -> float:
    if not word:
        return 1.0
    return float(_COUNT.get(word, word))


class HarmfulRules(EmergencyRules):
    def __init__(self, rules: dict):
        super().__init__(rules)
        self.extra_negations = [clean(x) for x in rules.get("extra_negations", [])]
        opened = rules.get("open_phrases", {})
        self.open_phrases = {clean(p) for p in opened.get("phrases", [])}
        self.ok_next = {clean(w) for w in opened.get("ok_next", [])}
        self.loss_re = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(w.lower()) for w in rules.get("loss_words") or ["\0"]) + r")(?![\w])")
        self.rate = rules.get("rate") or {}
        self.calories = rules.get("calories") or {}
        for cat in self.categories:                       # a reply that lost its helpline or "doctor" is a broken file, not a choice
            if not cat.get("reply") or cat.get("needs_line", "") not in cat["reply"]:
                raise ValueError(f"{rules.get('version')}: {cat['id']} reply must contain {cat.get('needs_line')!r}")
        for extra in (self.rate, self.calories):
            if extra and extra.get("category") not in self.by_id:
                raise ValueError(f"{rules.get('version')}: unknown category {extra.get('category')!r}")

    def _informational(self, clause: str, start: int, end: int) -> bool:
        """Also sets aside an open phrase with a food after it ("stop eating sugar", "skip all meals except dinner")."""
        if clause[start:end] in self.open_phrases:
            following = clause[end:].split()[:1]
            if following and following[0] not in self.ok_next:
                return True
        return super()._informational(clause, start, end)

    def speed(self, text: str) -> float | None:
        """The fastest weight loss the message asks about, in kg a week, or None. Needs a loss word in the same sentence."""
        fastest = None
        for cleaned in _clauses(text):
            if not self.loss_re.search(cleaned):
                continue
            for pattern in _SPEEDS:
                for m in pattern.finditer(cleaned):
                    weeks = _number(m.group("n")) * _WEEKS[m.group("u")]
                    kg = float(_KG_WORDS.get(m.group("kg"), m.group("kg")))
                    fastest = max(fastest or 0.0, kg / max(weeks, 1 / 7))
        return fastest

    def daily_kcal(self, text: str) -> int | None:
        """The lowest daily intake the message states ("500 calories a day"), or None. A deficit or exercise number is not intake."""
        lowest = None
        for cleaned in _clauses(text):
            for m in _KCAL_DAY.finditer(cleaned):
                if _NOT_INTAKE.search(cleaned[:m.start()]):
                    continue
                value = int(m.group(1))
                lowest = value if lowest is None else min(lowest, value)
        return lowest

    def check(self, text: str, negations) -> EmergencyResult:
        text = text if isinstance(text, str) else ""
        out = super().check(text, list(negations) + self.extra_negations)
        if self.rate:
            kg_week = self.speed(text)
            if kg_week is not None and kg_week > float(self.rate["max_kg_per_week"]):
                out.matched.append({"category": self.rate["category"], "how": "speed", "what": f"{kg_week:.1f} kg a week"})
        if self.calories:
            kcal = self.daily_kcal(text)
            if kcal is not None and kcal < int(self.calories["min_kcal_per_day"]):
                out.matched.append({"category": self.calories["category"], "how": "calories", "what": f"{kcal} kcal a day"})
        if out.matched:
            hit_ids = {m["category"] for m in out.matched}
            cat = next(c for c in self.categories if c["id"] in hit_ids)
            out.category, out.name, out.reply, out.checkin = cat["id"], cat["name"], cat["reply"], cat["checkin"]
        return out


__all__ = ["HarmfulRules"]
