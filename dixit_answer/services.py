"""Knowledge services (stage 6): protocol engine, food choices, cooking tips, food facts.

Each returns locked items that compose may arrange but not change. Recipes (hybrid sub-flow R1-R9) and the allergy filter
come in phase 5; until then recipe questions get approved cooking tips and an honest "no checked recipe yet".
"""
from __future__ import annotations

from dataclasses import dataclass

from .facts import Fact, make_fact, preferred_records
from .foods import FoodIndex
from .golden import GoldenRules, Status
from .loader import Library, SwasthamData

FACT_FIELDS = ("energy_kcal", "carbohydrate_g", "fiber_g", "protein_g")


@dataclass
class ChoiceGroup:
    guidance_id: str
    direction: str
    label: str
    foods: list          # food IDs (filtered)
    reason: str


class Services:
    def __init__(self, library: Library, data: SwasthamData, index: FoodIndex):
        self.library = library
        self.data = data
        self.index = index
        self.golden = GoldenRules(data.golden_rules)

    # ---------- protocol engine (Golden Rules, phase 6) ----------
    def protocol_full(self) -> list[str]:
        return self.golden.full()

    def protocol_reminder(self) -> str:
        return self.golden.reminder()

    def meal_place(self) -> str:
        return self.golden.meal_place()

    def protocol_amount(self, step: str) -> str | None:
        return self.golden.amount(step)

    def position_for(self, food_id: str, status: Status | None = None):
        """(locked text, fruit position or None). Fruit depends on diabetes status; unknown rules are said, never guessed."""
        return self.golden.position_for(food_id, self.index.foods[food_id], status or Status())

    def between_meals(self) -> str:
        return self.golden.between_meals()

    # ---------- food choices (approved list) ----------
    def choices(self, topic: str, blocked=frozenset()) -> list[ChoiceGroup]:
        """Approved groups for a topic, minus anything the allergy / avoid / diet filter blocks for this person."""
        groups = []
        for entry in self.data.approved_list["entries"]:
            if entry["topic"] != topic or not entry.get("golden_rule_checked"):
                continue
            foods = [f for f in entry["foods"] if f not in blocked]
            if foods:
                groups.append(ChoiceGroup(entry["guidance_id"], entry["direction"], entry["label"], foods, entry["reason"]))
        return groups

    def group_of(self, food_id: str) -> ChoiceGroup | None:
        for topic in ("sabji", "home_meal"):
            for group in self.choices(topic):
                if food_id in group.foods:
                    return group
        return None

    # ---------- cooking tips ----------
    def tips(self, keys: list[str], given: set[str], limit: int = 1) -> list[tuple[str, list[Fact], str]]:
        """Tips that apply to any of `keys` (food IDs or topics). Returns (text with {{A}}/{{B}}, facts, tip_id)."""
        env = self.library.environment
        out = []
        for tip in self.data.cooking_tips["tips"]:
            if not set(tip["applies_to"]) & set(keys):
                continue
            body = tip[env]
            facts = [make_fact(self.library, rid, fld) for rid, fld in body["compare"]]
            if any(f is None for f in facts):
                continue
            if facts and all(f"{f.record_id}:{f.field}" in given for f in facts):
                continue
            out.append((body["text"], facts, tip["tip_id"]))
            if len(out) >= limit:
                break
        return out

    # ---------- facts ----------
    def pick_fact(self, food_id: str, given: set[str], fields=FACT_FIELDS) -> Fact | None:
        """One fact for a food that hasn't been given in this conversation yet (R11)."""
        food = self.index.foods[food_id]
        for rid in preferred_records(food, self.library):
            for fld in fields:
                if f"{rid}:{fld}" in given:
                    continue
                fact = make_fact(self.library, rid, fld)
                if fact:
                    return fact
        return None
