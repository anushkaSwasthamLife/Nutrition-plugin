"""Golden Rules engine (phase 6).

Loads the Golden Rules as versioned data and answers three questions with locked text:
- what does the programme say about this food for this person? (status-aware for fruit)
- does this recipe break a Golden Rule?
- is this wording a claim that relaxes a Golden Rule?

Anything the data doesn't state is "unknown": the engine says so and points to the Dixit coach. It never guesses, and nothing
here can be widened by nutrition facts, swaps, recipes or the AI (NUT-GOLDEN-001).
"""
from __future__ import annotations

from dataclasses import dataclass

from .textnorm import clean, has_phrase

FRUIT_EXCEPTIONS = {"FOOD-LEMON"}              # a squeeze of lemon is seasoning, not fruit


@dataclass(frozen=True)
class Status:
    """A person's diabetes status as far as we know it. consultant_validated only matters after 'now normal'."""
    value: str = "unknown"                       # unknown | diabetic | prediabetic | non_diabetic | currently_non_diabetic
    prior_history: bool | None = None
    consultant_validated: bool | None = None

    @property
    def known(self) -> bool:
        return self.value != "unknown"


@dataclass(frozen=True)
class FruitPosition:
    allowed: str                                 # within_meal_window | no | unknown | depends_on_status
    text: str
    needs_status: bool = False                   # we would give a different answer once the status is known
    needs_consultant: bool = False


class GoldenRules:
    def __init__(self, data: dict):
        self.data = data
        self.version = data["version"]
        self.fruit_cfg = data["fruit"]
        self.positions = data["positions"]
        self.forbidden = [clean(p) for p in data.get("forbidden_claims", [])]

    # ------------------------------------------------------------- protocol text
    def full(self) -> list[str]:
        return list(self.data["full"])

    def reminder(self) -> str:
        return self.data["reminder"]

    def meal_place(self) -> str:
        return self.data["meal_place"]

    def amount(self, step: str, blocked=frozenset()) -> str | None:
        """The programme's amount for a step. Options the person can't have (allergy, diet, avoid list) are left out; if none
        is left, the line says to ask the Dixit coach (never an invented substitute)."""
        base = self.data["amounts"].get(step)
        cfg = self.data.get("amount_options", {}).get(step)
        if not cfg or not blocked:
            return base
        keep = [o["text"] for o in cfg["options"] if not set(o["foods"]) & set(blocked)]
        if len(keep) == len(cfg["options"]):
            return base
        if not keep:
            return cfg["none"]
        if len(keep) == 1:
            joined = keep[0]
        else:
            joined = ", ".join(keep[:-1]) + cfg["join"] + keep[-1]
        return joined + cfg["suffix"]

    def between_meals(self) -> str:
        return self.data.get("between_meals_allowed") or self.data["between_meals_unknown"]

    # ------------------------------------------------------------- fruit (status dependent)
    def fruit_position(self, status: Status) -> FruitPosition:
        cfg = self.fruit_cfg
        value = status.value
        if value == "non_diabetic":
            e = cfg["non_diabetic"]
            return FruitPosition(e["allowed"], e["text"])
        if value == "currently_non_diabetic":
            if status.consultant_validated is True:
                e = cfg["currently_non_diabetic_validated"]
                return FruitPosition(e["allowed"], e["text"] or cfg["unknown_rule_text"])
            e = cfg["currently_non_diabetic_unvalidated"]          # validated False or not yet told: stay restricted
            return FruitPosition(e["allowed"], e["text"], needs_consultant=status.consultant_validated is None)
        if value in ("diabetic", "prediabetic"):
            e = cfg[value]
            return FruitPosition(e["allowed"], e["text"] or cfg["unknown_rule_text"])
        e = cfg["unknown"]
        return FruitPosition(e["allowed"], e["text"], needs_status=True)

    # ------------------------------------------------------------- one food
    def is_fruit(self, category: str, food_id: str = "") -> bool:
        return category == "fruit" and food_id not in FRUIT_EXCEPTIONS

    def position_for(self, food_id: str, food: dict, status: Status) -> tuple[str, FruitPosition | None]:
        """(locked text, fruit position if the answer depends on it)."""
        category, name = food["category"], food["name"]
        if self.is_fruit(category, food_id):
            pos = self.fruit_position(status)
            return pos.text, pos
        template = self.positions.get(category)
        if not template:
            return self.data["unknown_position"].format(food=name), None
        return template.format(food=name[:1].upper() + name[1:]), None

    # ------------------------------------------------------------- recipes
    def recipe_violations(self, foods: list[str], index_foods: dict, status: Status, kind: str, meal_place: str) -> list[tuple[str, str]]:
        """[(tag, reason)] for a recipe. Conservative: fruit only passes for a confirmed non-diabetic status."""
        out: list[tuple[str, str]] = []
        if kind not in {"sabji", "dal", "khichdi", "chilla"} or meal_place != "home_meal":
            out.append(("golden_rule:meal_place", "it isn't a home-meal dish (the programme allows nothing between meals)"))
        if any(index_foods[f]["category"] == "sweet" for f in foods):
            out.append(("golden_rule:sugar", "it adds sugar (the programme asks you to reduce sugar and sweets)"))
        fruit = [f for f in foods if self.is_fruit(index_foods[f]["category"], f)]
        if fruit and status.value != "non_diabetic":
            out.append(("golden_rule:fruit", "it contains fruit, and the programme's fruit rule depends on your diabetes status"))
        return out

    # ------------------------------------------------------------- wording
    def relaxing_claims(self, text: str) -> list[str]:
        cleaned = clean(text)
        return [p for p in self.forbidden if has_phrase(cleaned, p)]
