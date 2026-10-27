"""Programme rules engine (phase 6; Dixit's Golden Rules are its first configuration).

Loads a programme's rules as versioned data and answers three questions with locked text:
- what does the programme say about this food for this person? (status-aware for a status food rule, such as Dixit's fruit rule)
- does this recipe break a programme rule?
- is this wording a claim that relaxes a programme rule?

The rules are optional types, switched on by the doctor pack (`programme.RULE_TYPES`): meal_order, food_positions,
status_food_rule, between_meals, home_meal_kinds, no_sweets_in_recipes, forbidden_claims. A type that is off adds no rule and no
programme text. Anything the data doesn't state is "unknown": the engine says so and points to the person to ask. It never guesses,
and nothing here can be widened by nutrition facts, swaps, recipes or the AI (NUT-GOLDEN-001).
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


ALL_TYPES = frozenset({"meal_order", "food_positions", "status_food_rule", "between_meals", "home_meal_kinds",
                       "no_sweets_in_recipes", "forbidden_claims"})


class GoldenRules:
    def __init__(self, data: dict, rule_types: set | None = None):
        self.data = data
        self.version = data.get("version", "none")
        self.types = frozenset(rule_types) if rule_types is not None else ALL_TYPES     # None = every type the data has
        self.fruit_cfg = data.get("fruit") or {}
        self.positions = data.get("positions") or {}
        self.home_meal_kinds = set(data.get("home_meal_kinds") or ("sabji", "dal", "khichdi", "chilla"))
        self.forbidden = [clean(p) for p in data.get("forbidden_claims", [])] if "forbidden_claims" in self.types else []
        self.status_category = data.get("status_rule_category", "fruit")
        self.status_exceptions = set(data.get("status_rule_exceptions") or FRUIT_EXCEPTIONS)

    @classmethod
    def empty(cls) -> "GoldenRules":
        """A programme with no rules of its own: plain nutrition and recipes, no programme wording."""
        return cls({"version": "none"}, set())

    def uses(self, rule_type: str) -> bool:
        return rule_type in self.types

    # ------------------------------------------------------------- protocol text (meal_order)
    def full(self) -> list[str]:
        return list(self.data["full"]) if self.uses("meal_order") else []

    def reminder(self) -> str:
        return self.data["reminder"] if self.uses("meal_order") else ""

    def meal_place(self) -> str:
        return self.data["meal_place"] if self.uses("meal_order") else ""

    def steps(self) -> list[str]:
        """The meal-order steps that have an amount, in order ("nuts", "salad", "sprouts")."""
        return list(self.data.get("amounts", {})) if self.uses("meal_order") else []

    def next_meal(self) -> str:
        return self.data.get("next_meal", "") if self.uses("meal_order") else ""

    def amount(self, step: str, blocked=frozenset()) -> str | None:
        """The programme's amount for a step. Options the person can't have (allergy, diet, avoid list) are left out; if none
        is left, the line says to ask the programme's coach (never an invented substitute)."""
        if not self.uses("meal_order"):
            return None
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
        """The programme's line on eating between meals, or "" when the programme has no such rule."""
        if not self.uses("between_meals"):
            return ""
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
        """Whether the status food rule covers this food (Dixit: fruit, except a squeeze of lemon). False when the rule is off."""
        return self.uses("status_food_rule") and category == self.status_category and food_id not in self.status_exceptions

    def position_for(self, food_id: str, food: dict, status: Status) -> tuple[str, FruitPosition | None]:
        """(locked text, fruit position if the answer depends on it). "" when the programme says nothing about foods."""
        category, name = food["category"], food["name"]
        if self.is_fruit(category, food_id):
            pos = self.fruit_position(status)
            return pos.text, pos
        if not self.uses("food_positions"):
            return "", None
        template = self.positions.get(category)
        if not template:
            return self.data["unknown_position"].format(food=name), None
        return template.format(food=name[:1].upper() + name[1:]), None

    # ------------------------------------------------------------- recipes
    def recipe_violations(self, foods: list[str], index_foods: dict, status: Status, kind: str, meal_place: str) -> list[tuple[str, str]]:
        """[(tag, reason)] for a recipe. Conservative: fruit only passes for a confirmed non-diabetic status."""
        out: list[tuple[str, str]] = []
        if self.uses("home_meal_kinds") and (kind not in self.home_meal_kinds or meal_place != "home_meal"):
            out.append(("golden_rule:meal_place", "it isn't a home-meal dish (the programme allows nothing between meals)"))
        if self.uses("no_sweets_in_recipes") and any(index_foods[f]["category"] == "sweet" for f in foods):
            out.append(("golden_rule:sugar", "it adds sugar (the programme asks you to reduce sugar and sweets)"))
        fruit = [f for f in foods if self.is_fruit(index_foods[f]["category"], f)]
        if fruit and status.value != "non_diabetic":
            out.append(("golden_rule:fruit", "it contains fruit, and the programme's fruit rule depends on your diabetes status"))
        return out

    # ------------------------------------------------------------- wording
    def relaxing_claims(self, text: str) -> list[str]:
        cleaned = clean(text)
        return [p for p in self.forbidden if has_phrase(cleaned, p)]
