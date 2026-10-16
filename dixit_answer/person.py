"""The person's profile and the allergy / avoid / diet filter (runs inside every service that names a food).

The profile comes from the Dixit app (read-only here). Anything the app doesn't send can be learnt in the chat and kept in
the chat state. A missing field never stops an answer: the safest option that doesn't need it is used instead.

The profile is outside input, so it is sanitised first: wrong types are dropped (never guessed), numbers must be finite and
sensible, lists are capped, and an allergy we can't map to a known allergen is treated as "be careful", never ignored.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .foods import FoodIndex
from .golden import Status
from .state import ChatState
from .textnorm import clean, has_phrase

JAIN_EXCLUDED = {"FOOD-ALOO", "FOOD-ONION", "FOOD-SWEET-POTATO", "FOOD-GARLIC", "FOOD-GINGER",
                 "FOOD-CARROT", "FOOD-BEETROOT", "FOOD-MOOLI", "FOOD-SHALGAM", "FOOD-ARBI", "FOOD-SURAN", "FOOD-SPRING-ONION",
                 "FOOD-MUSHROOM"}   # root vegetables, onion family and fungi; to confirm with the dietitian
MEDICINE_RISK = {"insulin": "diabetes_medicine", "sugar tablets": "diabetes_medicine", "metformin": "diabetes_medicine",
                 "glimepiride": "diabetes_medicine", "gliclazide": "diabetes_medicine", "glipizide": "diabetes_medicine",
                 "sulfonylurea": "diabetes_medicine", "bp": "bp_medicine", "amlodipine": "bp_medicine", "telmisartan": "bp_medicine",
                 "losartan": "bp_medicine", "blood pressure": "bp_medicine"}
DIET_TYPES = {"vegetarian", "egg", "non_vegetarian", "jain"}
STATUS_VALUES = {"unknown", "diabetic", "prediabetic", "non_diabetic", "currently_non_diabetic"}
TARGET_NUTRIENTS = {"energy_kcal", "carbohydrate_g", "fiber_g", "protein_g", "fat_g", "sodium_mg", "added_sugar_g", "sugars_g",
                    "saturated_fat_g", "cholesterol_mg", "potassium_mg", "phosphorus_mg"}
MAX_LIST = 30
MAX_TEXT = 60


def _strings(value, limit: int = MAX_LIST) -> list[str]:
    """A list of short non-empty strings; anything else in it is dropped."""
    if isinstance(value, str):
        value = [part for part in re.split(r"[,;/]", value)]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip() and len(item) <= 200:
            out.append(item.strip()[:MAX_TEXT])
        if len(out) >= limit:
            break
    return out


def _finite_positive(value, upper: float = 1e6):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 < number <= upper else None


def sanitize_profile(profile) -> dict:
    """Return a clean profile dict. Never raises. Missing / invalid fields are simply absent (= unknown)."""
    if not isinstance(profile, dict):
        return {}
    out: dict = {}
    if "allergies" in profile:
        raw = profile["allergies"]
        if isinstance(raw, (list, tuple, str)):
            out["allergies"] = _strings(raw)               # [] means "none"; a bad type means unknown (key left out)
    diet = profile.get("diet_type")
    if isinstance(diet, str) and diet.strip().lower() in DIET_TYPES:
        out["diet_type"] = diet.strip().lower()
    status = profile.get("diabetes_status")
    if isinstance(status, str) and status.strip().lower() in STATUS_VALUES:
        out["diabetes_status"] = status.strip().lower()
    for key in ("prior_diabetes_history", "consultant_validated", "pregnant"):
        if isinstance(profile.get(key), bool):
            out[key] = profile[key]
    if "conditions" in profile:
        out["conditions"] = [c.lower() for c in _strings(profile["conditions"], 10)]
    if "medicines" in profile and isinstance(profile["medicines"], (list, tuple, str)):
        out["medicines"] = _strings(profile["medicines"], 20)
    if "avoid_foods" in profile:
        out["avoid_foods"] = _strings(profile["avoid_foods"], 50)
    targets = profile.get("clinician_targets")
    if isinstance(targets, dict):
        clean_targets = {k: v for k, v in ((k, _finite_positive(v)) for k, v in targets.items()
                                           if isinstance(k, str) and k in TARGET_NUTRIENTS) if v is not None}
        if clean_targets:
            out["clinician_targets"] = clean_targets
    intake = profile.get("intake_today")
    if isinstance(intake, dict):
        clean_intake = {k: v for k, v in ((k, _finite_positive(v, 1e5)) for k, v in intake.items()
                                          if isinstance(k, str) and k in TARGET_NUTRIENTS) if v is not None}
        if clean_intake:
            out["intake_today"] = clean_intake
    logged = profile.get("intake_logged_at")
    if isinstance(logged, str) and len(logged) <= 40:
        out["intake_logged_at"] = logged
    return out


@dataclass
class Person:
    allergies: list | None = None          # None = unknown, [] = none
    unmapped_allergies: list = field(default_factory=list)   # allergies we couldn't map: be careful, don't ignore
    avoid_foods: list = field(default_factory=list)   # food IDs
    diet_type: str = "unknown"             # vegetarian | egg | non_vegetarian | jain | unknown
    conditions: list = field(default_factory=list)
    clinician_targets: dict = field(default_factory=dict)   # nutrient -> daily max (only if the app holds them)
    intake_today: dict = field(default_factory=dict)        # nutrient -> amount, only used if logged recently
    intake_logged_at: str | None = None
    status: Status = field(default_factory=Status)          # diabetes status for the Golden Rules (fruit)

    @property
    def allergies_known(self) -> bool:
        return self.allergies is not None

    def recent_intake(self, now: datetime | None = None) -> dict:
        """Intake counts only if logged today within the last 12 hours; otherwise it's ignored (never 'nothing eaten')."""
        if not self.intake_today or not self.intake_logged_at:
            return {}
        try:
            logged = datetime.fromisoformat(self.intake_logged_at)
        except (ValueError, TypeError):
            return {}
        now = now or datetime.now()
        try:
            if logged.date() != now.date() or now - logged > timedelta(hours=12) or logged > now + timedelta(minutes=5):
                return {}
        except TypeError:                                  # timezone-aware vs naive
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
        if person.unmapped_allergies:                       # an allergy we can't map: stay clear of every common allergen
            blocked |= self.major_allergen_foods()
        if person.diet_type in ("vegetarian", "jain"):
            blocked |= {f for f, food in self.index.foods.items() if food["category"] in {"egg", "meat", "fish"}}
        if person.diet_type == "jain":
            blocked |= JAIN_EXCLUDED
        return blocked

    def major_allergen_foods(self) -> set[str]:
        """Used while allergies are unknown: only recipes free of all major allergens."""
        return self.foods_for(self.map["major"])

    def canonical(self, entries: list[str]) -> tuple[list[str], list[str]]:
        """Map free-text allergy entries ('Milk', 'dairy', 'lactose') to allergen keys. Returns (keys, unmapped entries)."""
        keys: list[str] = []
        unmapped: list[str] = []
        for entry in entries:
            low = entry.strip().lower().replace(" ", "_")
            if low in self.map["allergens"]:
                found = [low]
            else:
                found = self.read_reply(entry) or []
            if found:
                keys += [k for k in found if k not in keys]
            elif clean(entry) in {clean(w) for w in self.map["none_words"]}:
                continue
            else:
                unmapped.append(entry)
        return keys, unmapped

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


def build_person(profile: dict | None, state: ChatState, index: FoodIndex, allergy: AllergyFilter | None = None) -> Person:
    """Profile from the app first; fall back to what the chat learnt. The profile is sanitised here as well."""
    profile = sanitize_profile(profile)
    allergies, unmapped = profile.get("allergies"), []
    if allergies is not None and allergy is not None:
        allergies, unmapped = allergy.canonical(allergies)
    elif allergies is not None:
        unmapped = list(allergies)
        allergies = []
    if allergies is None:
        allergies = state.allergies if isinstance(state.allergies, list) else None
        allergies = [a for a in allergies if isinstance(a, str)] if allergies is not None else None
    avoid: list[str] = []
    for item in profile.get("avoid_foods") or []:
        found = [m.food_id for m in index.find(str(item))]
        avoid += found or ([item] if item in index.foods else [])
    diet = profile.get("diet_type") or (state.food_preference if state.food_preference in DIET_TYPES else "unknown")
    conditions = list(dict.fromkeys([*(profile.get("conditions") or []), *state.conditions_mentioned]))
    value = profile.get("diabetes_status") or state.diabetes_status
    if value not in STATUS_VALUES:
        value = "unknown"
    prior = profile.get("prior_diabetes_history", state.prior_diabetes_history)
    validated = profile.get("consultant_validated", state.consultant_validated)
    status = Status(value, prior if isinstance(prior, bool) else None, validated if isinstance(validated, bool) else None)
    if status.value in ("diabetic", "prediabetic") and "diabetes" not in conditions:
        conditions.append("diabetes")
    return Person(allergies=list(allergies) if allergies is not None else None, unmapped_allergies=unmapped, avoid_foods=avoid,
                  diet_type=diet, status=status, conditions=conditions,
                  clinician_targets=dict(profile.get("clinician_targets") or {}),
                  intake_today=dict(profile.get("intake_today") or {}), intake_logged_at=profile.get("intake_logged_at"))


def apply_profile_safety(profile: dict | None, state: ChatState) -> None:
    """Medicines and pregnancy from the app's profile feed the safety pre-check."""
    profile = sanitize_profile(profile)
    meds = profile.get("medicines")
    if meds is not None:
        for med in meds:
            cleaned = clean(med)
            for phrase, key in MEDICINE_RISK.items():
                if has_phrase(cleaned, phrase):
                    state.safety_status[key] = "yes"
        if meds == []:
            for key in ("diabetes_medicine", "bp_medicine"):
                if state.safety_status.get(key) != "yes":
                    state.safety_status[key] = "no"
    if "pregnant" in profile:
        state.safety_status["pregnancy"] = "yes" if profile["pregnant"] else "no"
