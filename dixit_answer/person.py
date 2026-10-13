"""The person's profile and the allergy / avoid / diet filter (runs inside every service that names a food).

The profile comes from the Dixit app (read-only here). Anything the app doesn't send can be learnt in the chat and kept in
the chat state. A missing field never stops an answer: the safest option that doesn't need it is used instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .foods import FoodIndex
from .state import ChatState
from .textnorm import clean, has_phrase

JAIN_EXCLUDED = {"FOOD-ALOO", "FOOD-ONION", "FOOD-SWEET-POTATO", "FOOD-GARLIC", "FOOD-GINGER"}   # root vegetables; to confirm
MEDICINE_RISK = {"insulin": "diabetes_medicine", "sugar tablets": "diabetes_medicine", "metformin": "diabetes_medicine",
                 "glimepiride": "diabetes_medicine", "gliclazide": "diabetes_medicine", "bp": "bp_medicine"}


@dataclass
class Person:
    allergies: list | None = None          # None = unknown, [] = none
    avoid_foods: list = field(default_factory=list)   # food IDs
    diet_type: str = "unknown"             # vegetarian | egg | non_vegetarian | jain | unknown
    conditions: list = field(default_factory=list)
    clinician_targets: dict = field(default_factory=dict)   # nutrient -> daily max (only if the app holds them)
    intake_today: dict = field(default_factory=dict)        # nutrient -> amount, only used if logged recently
    intake_logged_at: str | None = None

    @property
    def allergies_known(self) -> bool:
        return self.allergies is not None

    def recent_intake(self, now: datetime | None = None) -> dict:
        """Intake counts only if logged today within the last 12 hours; otherwise it's ignored (never 'nothing eaten')."""
        if not self.intake_today or not self.intake_logged_at:
            return {}
        try:
            logged = datetime.fromisoformat(self.intake_logged_at)
        except ValueError:
            return {}
        now = now or datetime.now()
        if logged.date() != now.date() or now - logged > timedelta(hours=12):
            return {}
        return dict(self.intake_today)


class AllergyFilter:
    def __init__(self, allergen_map: dict, index: FoodIndex):
        self.map = allergen_map
        self.index = index

    def foods_for(self, allergens: list[str]) -> set[str]:
        blocked: set[str] = set()
        for allergen in allergens:
            blocked.update(self.map["allergens"].get(allergen, {}).get("foods", []))
            for fid, food in self.index.foods.items():
                if allergen in food.get("allergens", []):
                    blocked.add(fid)
        return blocked

    def blocked_foods(self, person: Person) -> set[str]:
        """Food IDs this person must never be offered: allergies (with derivatives), avoid list, diet type."""
        allergens = person.allergies if person.allergies is not None else []
        blocked = self.foods_for(allergens) | set(person.avoid_foods)
        if person.diet_type in ("vegetarian", "jain"):
            blocked |= {f for f, food in self.index.foods.items() if food["category"] in {"egg", "meat", "fish"}}
        if person.diet_type == "jain":
            blocked |= JAIN_EXCLUDED
        return blocked

    def major_allergen_foods(self) -> set[str]:
        """Used while allergies are unknown: only recipes free of all major allergens."""
        return self.foods_for(self.map["major"])

    def read_reply(self, text: str) -> list[str] | None:
        """Allergies from a reply ('milk and peanuts', 'none'). None if the reply says nothing about allergies."""
        cleaned = clean(text)
        found = [name for name, entry in self.map["allergens"].items()
                 if any(has_phrase(cleaned, clean(w)) for w in entry["words"])]
        if found:
            return found
        if any(has_phrase(cleaned, clean(w)) for w in self.map["none_words"]):
            return []
        return None

    def label(self, allergen: str) -> str:
        return self.map["allergens"].get(allergen, {}).get("label", allergen)


def build_person(profile: dict | None, state: ChatState, index: FoodIndex) -> Person:
    """Profile from the app first; fall back to what the chat learnt."""
    profile = profile or {}
    allergies = profile.get("allergies")
    if allergies is None:
        allergies = state.allergies
    avoid = []
    for item in profile.get("avoid_foods") or []:
        avoid += [m.food_id for m in index.find(str(item))] or ([item] if item in index.foods else [])
    diet = profile.get("diet_type") or (state.food_preference if state.food_preference != "unknown" else "unknown")
    conditions = list(dict.fromkeys([*(profile.get("conditions") or []), *state.conditions_mentioned]))
    return Person(allergies=list(allergies) if allergies is not None else None, avoid_foods=avoid, diet_type=diet,
                  conditions=conditions, clinician_targets=dict(profile.get("clinician_targets") or {}),
                  intake_today=dict(profile.get("intake_today") or {}), intake_logged_at=profile.get("intake_logged_at"))


def apply_profile_safety(profile: dict | None, state: ChatState) -> None:
    """Medicines and pregnancy from the app's profile feed the safety pre-check."""
    if not profile:
        return
    for med in profile.get("medicines") or []:
        key = MEDICINE_RISK.get(str(med).strip().lower())
        if key:
            state.safety_status[key] = "yes"
    if profile.get("medicines") == []:
        state.safety_status["diabetes_medicine"] = state.safety_status.get("diabetes_medicine") if \
            state.safety_status.get("diabetes_medicine") == "yes" else "no"
        state.safety_status["bp_medicine"] = state.safety_status.get("bp_medicine") if \
            state.safety_status.get("bp_medicine") == "yes" else "no"
    if profile.get("pregnant") is not None:
        state.safety_status["pregnancy"] = "yes" if profile["pregnant"] else "no"
