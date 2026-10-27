"""General recipe fallback: a dish with no approved recipe, written by the model and checked by code (development only).

The model returns a name, a cooking method, plain ingredient names and 2 to 6 steps: no amounts and no nutrition numbers, because
the ingredients are free text and cannot be calculated. Before anything is shown the code checks every word against the person's
allergies, diet, avoid list, the condition word lists, the Golden Rules (sugar, sweets, fruit) and the wording checks. Anything that
fails is retried once with the reasons, then dropped. The answer carries a label that says the nutrition was not calculated.
Rules and wording live in data/general_recipe_rules.json (PROPOSED: production refuses it, and recipe drafting is off there).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .lists import method_allowed
from .person import Person
from .programme import current as programme
from .recipes import _only_minutes, _plain_text
from .textnorm import clean, has_phrase

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{1,39}$")
_CLAIM_KEYS = ("rule_relaxing", "personal_claims", "outcome_claims", "portion_phrases", "injection_markers")
TIME_BUDGET_SECONDS = 20


@dataclass
class GeneralRecipe:
    name: str
    method: str
    ingredients: list
    steps: list
    label: str


@dataclass
class GeneralOutcome:
    status: str                       # recipe | declined | skipped
    recipe: GeneralRecipe | None = None
    reasons: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    attempts: int = 0
    request: str = ""


def _hit(cleaned: str, words) -> list[str]:
    """Words of the list found in the text as whole words, also as a simple plural ("egg" finds "eggs")."""
    found = []
    for word in words:
        phrase = clean(word)
        if phrase and (has_phrase(cleaned, phrase) or has_phrase(cleaned, phrase + "s") or has_phrase(cleaned, phrase + "es")):
            found.append(word)
    return found


class GeneralRecipeService:
    def __init__(self, llm, rules: dict, allergy, index, words: dict, white_lists: dict, environment: str, golden,
                 enabled: bool):
        self.llm, self.rules, self.allergy, self.index = llm, rules, allergy, index
        self.words, self.white, self.environment = words, white_lists, environment
        self.enabled = enabled
        self.limits = rules["limits"]
        self.diet = rules["diet_words"]

    # ------------------------------------------------------------------ what the person must not get
    @property
    def golden(self):
        """The rules of the patient's programme (from the doctor pack), for this request."""
        return programme().rules

    def _diet_words(self, person: Person) -> list[str]:
        if person.diet_type in ("vegetarian", "jain"):
            words = self.diet["meat"] + self.diet["fish"] + self.diet["egg"]
        elif person.diet_type == "egg":
            words = self.diet["meat"] + self.diet["fish"]
        else:
            words = []
        return words + (self.diet["jain_extra"] if person.diet_type == "jain" else [])

    def never_use(self, person: Person) -> list[str]:
        """Plain ingredient words the model is told to keep away from (no health information is sent, only the words)."""
        words = list(self._diet_words(person))
        for allergen in person.allergies or []:
            entry = self.allergy.map["allergens"].get(allergen, {})
            words += entry.get("words", [])
        words += [self.index.name(f) for f in person.avoid_foods if f in self.index.foods]
        words += self.rules["sugar_words"][:8]
        return sorted(dict.fromkeys(w for w in words if isinstance(w, str)))

    def problems(self, text: str, person: Person, blocked: set[str], what: str = "it") -> list[str]:
        """Reasons this text (a name, an ingredient or a step) cannot be shown to this person. Empty = nothing found."""
        cleaned = clean(text, self.words["spelling_map"])
        out: list[str] = []
        diet = _hit(cleaned, self._diet_words(person))
        if diet:
            out.append(f"{what} has {diet[0]}, which doesn't suit your {person.diet_type.replace('_', '-')} diet")
        named = {m.food_id for m in self.index.find(cleaned, already_clean=True)}
        for fid in sorted(named & blocked):
            out.append(f"{what} has {self.index.name(fid)}, which isn't suitable for you")
        wanted = set(person.allergies or [])
        for allergen in self.allergy.read_reply(text) or []:
            if allergen in wanted:
                out.append(f"{what} has something with {self.allergy.label(allergen)}, which you are allergic to")
        sugar = _hit(cleaned, self.rules["sugar_words"])
        sweet = [fid for fid in named if self.index.foods[fid]["category"] == "sweet"]
        if sugar or sweet:
            out.append(f"{what} adds sugar or sweets (the programme asks you to reduce them)")
        if person.status.value != "non_diabetic" and any(self.golden.is_fruit(self.index.foods[f]["category"], f) for f in named):
            out.append(f"{what} has fruit, and the programme's fruit rule depends on your diabetes status")
        for condition in person.conditions:
            hits = _hit(cleaned, self.rules["condition_words"].get(condition, []))
            if hits:
                out.append(f"{what} has {hits[0]}, which our list asks you to avoid with {condition.replace('_', ' ')}")
        return out

    # ------------------------------------------------------------------ validate the model's draft
    def _validate(self, draft, person: Person, blocked: set[str]) -> tuple[GeneralRecipe | None, list[str]]:
        try:
            return self._validate_unsafe(draft, person, blocked)
        except Exception:                                  # noqa: BLE001 - whatever shape came back, it is rejected
            return None, ["the draft was malformed"]

    def _validate_unsafe(self, draft, person: Person, blocked: set[str]) -> tuple[GeneralRecipe | None, list[str]]:
        lim = self.limits
        if not isinstance(draft, dict):
            return None, ["the draft is not an object"]
        name, method, items, steps = draft.get("name"), draft.get("method"), draft.get("ingredients"), draft.get("steps")
        if not isinstance(name, str) or not 3 <= len(name.strip()) <= 60 or not _plain_text(name) or any(c.isdigit() for c in name):
            return None, ["the name must be 3 to 60 plain letters with no numbers, links or markup"]
        if not isinstance(method, str) or not method_allowed(self.white, method, self.environment):
            return None, ["the cooking method must be one of the allowed methods"]
        if not isinstance(items, list) or not lim["ingredients_min"] <= len(items) <= lim["ingredients_max"]:
            return None, [f"give {lim['ingredients_min']} to {lim['ingredients_max']} ingredients"]
        if not isinstance(steps, list) or not lim["steps_min"] <= len(steps) <= lim["steps_max"] \
                or not all(isinstance(s, str) and 5 <= len(s) <= 220 and _plain_text(s) for s in steps):
            return None, [f"give {lim['steps_min']} to {lim['steps_max']} plain steps of 5 to 220 characters"]
        ingredients, seen, reasons = [], set(), []
        for item in items:
            if not isinstance(item, str) or not _NAME_RE.match(item.strip()):
                return None, ["each ingredient must be a plain name (letters only, no amounts)"]
            plain = clean(item)
            if plain in seen:
                return None, [f"{item.strip()} is listed twice"]
            seen.add(plain)
            if _hit(plain, self.rules["quantity_words"]) or _hit(plain, self.rules["number_words"]):
                return None, [f"'{item.strip()}' carries an amount; ingredients are names only"]
            ingredients.append(item.strip())
        if not all(_only_minutes(s) for s in steps):
            return None, ["steps may carry numbers only as cooking times such as 10 minutes"]
        every = clean(" ".join(steps))
        if _hit(every, self.rules["quantity_words"]):
            return None, ["steps must not carry amounts or measures"]
        for key in _CLAIM_KEYS:
            claim = [w for w in self.words.get(key, []) if has_phrase(every + " " + clean(name), clean(w))]
            if claim:
                return None, [f"steps or name contain wording that is not allowed: {claim[0]}"]
        claim = _hit(every + " " + clean(name), self.rules["claim_words"])
        if claim:
            return None, [f"no health claims ({claim[0]})"]
        if self.golden.relaxing_claims(" ".join([name, *steps])):
            return None, ["the wording relaxes a programme rule"]
        for text, what in [(name, "the name"), *[(i, i) for i in ingredients], *[(s, "a step") for s in steps]]:
            reasons += self.problems(text, person, blocked, what)
        if reasons:
            return None, list(dict.fromkeys(reasons))
        # steps may only name foods that are in the ingredient list (the same rule as N26 for approved recipes)
        listed = clean(" ".join(ingredients))
        stray = [w for w in self._food_words(every) if not has_phrase(listed, clean(w))]
        if stray:
            return None, [f"steps mention {stray[0]}, which isn't in the ingredients"]
        return GeneralRecipe(name.strip(), method.strip(), ingredients, [s.strip() for s in steps], self.rules["label"]), []

    def _food_words(self, cleaned: str) -> list[str]:
        """Names of foods from the food master that appear in the steps (pantry items like salt and oil are exempt)."""
        found = []
        for m in self.index.find(cleaned, already_clean=True):
            if self.index.foods[m.food_id]["category"] != "pantry" and m.alias not in found:
                found.append(m.alias)
        return found

    # ------------------------------------------------------------------ the flow
    def run(self, dish_request: str, person: Person, blocked: set[str]) -> GeneralOutcome:
        out = GeneralOutcome("skipped", request=dish_request[:80])
        if not self.enabled:
            out.trace.append("G0: general recipes are off in this environment")
            return out
        if getattr(self.llm, "name", "mock") == "mock":
            out.trace.append("G0: no language model, so no general recipe")
            return out
        if not person.allergies_known or person.unmapped_allergies:
            out.trace.append("G1: allergies unknown or not recognised, so no general recipe")
            out.reasons = ["allergies"]
            return out
        request_problems = self.problems(dish_request, person, blocked, "the dish")
        if any(has_phrase(clean(dish_request), clean(w)) for w in self.words.get("injection_markers", [])):
            request_problems.append("the request reads like an instruction")
        if request_problems:
            out.status, out.reasons = "declined", request_problems
            out.trace.append(f"G1: request refused before drafting: {request_problems}")
            return out
        methods = sorted({e["id"] for e in (self.white.get("cooking_methods", {}).get("entries") or [])
                          if isinstance(e, dict) and self.environment in (e.get("allowed_in") or []) and e.get("id") != "raw"})
        started, feedback = time.monotonic(), []
        for attempt in range(self.limits["attempts"]):
            if time.monotonic() - started > TIME_BUDGET_SECONDS:
                out.trace.append("G2: time budget used up")
                break
            out.attempts += 1
            request = {"dish_request": dish_request[:80], "never_use": self.never_use(person), "allowed_methods": methods,
                       "previous_attempt_failed": feedback}
            try:
                draft = self.llm.draft_general_recipe(request)
            except Exception as exc:                       # noqa: BLE001 - a provider error means no recipe, never a crash
                out.trace.append(f"G2: draft failed ({exc.__class__.__name__}: {str(exc)[:80]})")
                break
            if draft is None:
                out.trace.append("G2: the model gave no draft")
                break
            recipe, reasons = self._validate(draft, person, blocked)
            if recipe is not None:
                out.status, out.recipe = "recipe", recipe
                out.trace.append(f"G3: attempt {attempt + 1} passed the checks")
                return out
            out.trace.append(f"G3: attempt {attempt + 1} rejected: {reasons}")
            feedback = reasons
        out.status, out.reasons = "declined", feedback or ["no draft passed the checks"]
        return out
