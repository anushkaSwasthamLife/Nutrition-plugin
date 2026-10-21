"""Dishes near a nutrient target (stage 6e): "a meal with 326 kcal and 26 g protein, what can I have".

Read the numbers the person gave (only a number tied to a unit or a nutrient: "326kcal", "26g protein", "under 400 calories"), then
list library dishes whose stored values per serving sit close to them. This is a look-up of stored facts, not a meal plan: nothing
is combined, scaled or invented, every number shown is a locked fact from the dish's own record, and a dish that the person cannot
have (allergy, diet, foods to avoid) is never listed. Production has no Indian dishes (licence pending), so it finds none.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dish_lookup import FOUND, DishLookup

NUM = r"(\d{1,4}(?:\.\d+)?)"
G = r"(?:g|gm|gms|gram|grams)"
NUTRIENT = r"(protein|proteins|carb|carbs|carbohydrate|carbohydrates|fat|fats|fibre|fiber)"
FIELD = {"protein": "protein_g", "proteins": "protein_g", "carb": "carb_g", "carbs": "carb_g", "carbohydrate": "carb_g", "carbohydrates": "carb_g",
         "fat": "fat_g", "fats": "fat_g", "fibre": "fibre_g", "fiber": "fibre_g"}
LIMITS = {"energy_kcal": (20, 3000), "protein_g": (1, 300), "carb_g": (1, 400), "fat_g": (1, 250), "fibre_g": (1, 100)}
LABEL = {"energy_kcal": "calories", "protein_g": "g of protein", "carb_g": "g of carbohydrate", "fat_g": "g of fat", "fibre_g": "g of fibre"}
WANT = re.compile(r"\b(meal|meals|dish|dishes|food|foods|something|anything|recipe|recipes|options?|what can i (?:have|eat|cook|make)|can i have|"
                  r"i want|i need|give me|suggest|show me|looking for)\b")
MAX = re.compile(r"\b(?:under|below|less than|upto|up to|at most|max|maximum|not more than|within)\s*$")
MIN = re.compile(r"\b(?:over|above|more than|at least|min|minimum|atleast|not less than)\s*$")
# non-veg words: a dish with one of them in its name or an ingredient is not offered to anyone who has not said they eat it
MEAT_FISH = {"chicken", "mutton", "lamb", "goat", "beef", "pork", "ham", "bacon", "sausage", "keema", "kheema", "gosht", "murgh", "fish", "machli", "prawn",
             "prawns", "shrimp", "crab", "squid", "salmon", "tuna", "pomfret", "rohu", "hilsa", "seafood", "meat", "turkey", "duck", "liver", "mince", "minced",
             "anchovy", "sardine", "mackerel", "lobster", "oyster", "clam", "mussel"}
EGG = {"egg", "eggs", "anda", "omelette", "omelet", "bhurji"}


@dataclass
class Target:
    field: str
    value: float
    mode: str = "about"            # about | max | min


@dataclass
class Match:
    code: str
    name: str
    result: object
    error: float
    values: dict = field(default_factory=dict)


def parse_targets(cleaned: str) -> list[Target]:
    """The nutrient amounts a message asks for. Only a number with a unit (kcal, calories, g) or a nutrient counts."""
    found: dict[str, Target] = {}

    def add(fld: str, raw: str, before: str) -> None:
        value = float(raw)
        low, high = LIMITS[fld]
        if not low <= value <= high or fld in found:
            return
        mode = "max" if MAX.search(before) else "min" if MIN.search(before) else "about"
        found[fld] = Target(fld, value, mode)

    for m in re.finditer(rf"{NUM}\s*(?:kcal|calories|calorie|cals|cal)\b", cleaned):
        add("energy_kcal", m.group(1), cleaned[:m.start()])
    for m in re.finditer(rf"(?:calories|calorie|kcal|energy)\s*(?:of|around|about|is|=|:)?\s*{NUM}\b(?!\s*{G}\b)", cleaned):
        add("energy_kcal", m.group(1), cleaned[:m.start()])
    for m in re.finditer(rf"{NUM}\s*{G}\s*(?:of\s+)?{NUTRIENT}\b", cleaned):
        add(FIELD[m.group(2)], m.group(1), cleaned[:m.start()])
    for m in re.finditer(rf"{NUTRIENT}\s*(?:of|around|about|is|=|:)?\s*{NUM}\s*{G}\b", cleaned):
        add(FIELD[m.group(1)], m.group(2), cleaned[:m.start()])
    return list(found.values())


def is_target_request(cleaned: str) -> list[Target]:
    targets = parse_targets(cleaned)
    return targets if targets and WANT.search(cleaned) else []


def _words(texts) -> set[str]:
    return {w for t in texts for w in re.findall(r"[a-z]+", str(t).lower())}


class TargetSearch:
    def __init__(self, dishes: DishLookup, allergy, index):
        self.dishes, self.allergy, self.index = dishes, allergy, index

    def _allowed(self, code: str, person, blocked: set[str], allergens: set[str], leave_out: set[str] = frozenset()) -> bool:
        names = [self.dishes.label(code), *self.dishes.ingredient_names(code)]
        words = _words(names)
        if leave_out and (words & leave_out or {w.rstrip("s") for w in words} & {w.rstrip("s") for w in leave_out}):
            return False
        diet = getattr(person, "diet_type", "unknown") if person is not None else "unknown"
        if words & MEAT_FISH and diet != "non_vegetarian":
            return False
        if words & EGG and diet not in {"egg", "non_vegetarian"}:
            return False
        rec = self.dishes.library.records.get(self.dishes.by_code.get(code, ""), {})
        if "nonveg" in str(rec.get("category", "")).replace("_", "").lower() and diet != "non_vegetarian":
            return False
        text = " , ".join(names)
        if blocked & {m.food_id for m in self.index.find(text)}:
            return False
        if allergens:
            if not self.dishes.ingredient_names(code):
                return False                                    # no ingredient list: cannot show it is free of the allergen
            if set(self.allergy.read_reply(text) or []) & allergens:
                return False
        return True

    def search(self, targets: list[Target], person, blocked: set[str], limit: int = 3, leave_out=(), skip_codes=()) -> tuple[list[Match], bool]:
        """(best matches, whether they are within the normal tolerance). Dishes with no clear serving or only an estimate are left out."""
        rules = self.dishes.rules.get("range_percent", {})
        tight = float(rules.get("default", 15)) / 100.0
        wide = tight * 2
        allergens: set[str] = set(person.allergies or []) if person is not None else set()
        if person is None or person.allergies is None or person.unmapped_allergies:
            allergens |= set(self.allergy.map["major"])          # allergies unknown or not understood: stay clear of every common one
        leave = {str(w).lower() for w in leave_out}
        skip = set(skip_codes)
        scored: list[Match] = []
        for code in self.dishes.by_code:
            if code in skip:
                continue
            result = self.dishes.values(code)
            if result.status != FOUND or result.estimate or result.per_serving is None or not result.serving_text:
                continue
            ps = result.per_serving
            errors = []
            for t in targets:
                v = ps.get(t.field)
                if v is None:
                    errors = None
                    break
                if t.mode == "max":
                    errors.append(0.0 if v <= t.value else (v - t.value) / t.value)
                elif t.mode == "min":
                    errors.append(0.0 if v >= t.value else (t.value - v) / t.value)
                else:
                    errors.append(abs(v - t.value) / t.value)
            if errors is None:
                continue
            scored.append(Match(code, self.dishes.label(code), result, max(errors), {t.field: ps[t.field] for t in targets}))
        scored.sort(key=lambda m: (m.error, m.name))
        for tol in (tight, wide):
            picked: list[Match] = []
            seen: set[str] = set()
            for m in scored:
                if m.error > tol:
                    break
                key = m.name.split(" (")[0].lower()
                if key in seen or not self._allowed(m.code, person, blocked, allergens, leave):
                    continue
                seen.add(key)
                picked.append(m)
                if len(picked) >= limit:
                    break
            if picked:
                return picked, tol == tight
        return [], False
