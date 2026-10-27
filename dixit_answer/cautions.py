"""Food cautions for a condition, a medicine or a pregnancy (`data/food_cautions_test.json`, doctor to review).

When the person has one of the listed conditions or medicines, or is pregnant, and the answer names one of the rule's foods, the
answer gets the rule's fixed line: a caution and "check with your doctor", never a ban, an amount or a dose. No AI is used.
"""
from __future__ import annotations

import re

from .textnorm import clean, has_phrase

NEGATIONS = r"(?:not|no|never|without|avoid|avoiding|dont|don t|do not|cannot|can t)"     # "I don't eat raw papaya"


class FoodCautions:
    def __init__(self, data: dict, word_lists: dict, foods: dict):
        self.version = data["version"]
        self.rules = data["cautions"]
        self.names = {fid: [clean(n) for n in [food["name"], *food.get("aliases", [])]] for fid, food in foods.items()}
        named = {**word_lists["conditions"], "kidney": word_lists["risk_terms"]["kidney_disease"]}     # "chronic kidney disease", "ckd"
        self.condition_words = {key: [clean(w) for w in [key, *words]] for key, words in named.items()}

    def _applies(self, when: dict, person) -> bool:
        if "condition" in when:
            words = self.condition_words.get(when["condition"], [clean(when["condition"])])
            return any(has_phrase(clean(c), w) for c in person.conditions if isinstance(c, str) for w in words)
        if "medicine" in when:
            return any(has_phrase(clean(m), clean(name)) for m in person.medicines if isinstance(m, str) for name in when["medicine"])
        if "pregnant" in when:
            return person.pregnant is True
        return False

    @staticmethod
    def _said(said: str, name: str) -> bool:
        """The food is named in the message and not ruled out just before ("no papaya", "I don't eat papaya")."""
        if not has_phrase(said, name):
            return False
        return re.search(rf"(?<!\w){NEGATIONS}\s+(?:\w+\s+){{0,2}}{re.escape(name)}(?!\w)", said) is None

    def lines(self, person, food_ids, name, text: str = "") -> list[tuple[str, str]]:
        """[(rule id, line)] for the rules that apply to this person and name a food in the answer, or in the message (a food that is
        not read in the chat, such as raw papaya, is still named there), in file order."""
        if person is None:
            return []
        said = clean(text)
        out = []
        for rule in self.rules:
            hit = [f for f in rule["foods"] if f in food_ids or any(self._said(said, n) for n in self.names.get(f, []))]
            if hit and self._applies(rule["when"], person):
                out.append((rule["id"], rule["line"].replace("{foods}", " and ".join(name(f) for f in hit))))
        return out

    def problems(self) -> list[str]:
        """Start-up checks: every food exists, every rule has one known condition, medicine list or pregnancy."""
        out = []
        for rule in self.rules:
            out += [f"food caution {rule['id']}: unknown food {f}" for f in rule["foods"] if f not in self.names]
            when = rule.get("when") or {}
            if len(when) != 1 or next(iter(when)) not in {"condition", "medicine", "pregnant"}:
                out.append(f"food caution {rule['id']}: 'when' needs exactly one of condition, medicine, pregnant")
            elif "condition" in when and when["condition"] not in self.condition_words:
                out.append(f"food caution {rule['id']}: unknown condition {when['condition']!r}")
        return out


__all__ = ["FoodCautions"]
