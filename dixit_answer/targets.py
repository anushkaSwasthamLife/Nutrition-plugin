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
                  r"i want|i need|need|want|require|requires|give me|suggest|show me|looking for)\b")
MAX = re.compile(r"\b(?:under|below|less than|upto|up to|at most|max|maximum|not more than|within)\s*$")
MIN = re.compile(r"\b(?:over|above|more than|at least|min|minimum|atleast|not less than)\s*$")
# non-veg words: a dish with one of them in its name or an ingredient is not offered to anyone who has not said they eat it
MEAT_FISH = {"chicken", "mutton", "lamb", "goat", "beef", "pork", "ham", "bacon", "sausage", "keema", "kheema", "gosht", "murgh", "fish", "machli", "prawn",
             "prawns", "shrimp", "crab", "squid", "salmon", "tuna", "pomfret", "rohu", "hilsa", "seafood", "meat", "turkey", "duck", "liver", "mince", "minced",
             "anchovy", "sardine", "mackerel", "lobster", "oyster", "clam", "mussel"}
EGG = {"egg", "eggs", "anda", "omelette", "omelet", "bhurji"}
# a meat or fish named as the wish itself ("i want chicken", "fish khana hai"): the search keeps to dishes with it. Eggs are a known food
# with their own answer, and "liver", "ham", "mince" and the like are not things people ask for by name.
WISH_WORDS = {"chicken", "mutton", "lamb", "goat", "beef", "pork", "keema", "kheema", "fish", "prawn", "prawns", "shrimp", "crab", "squid", "salmon",
              "tuna", "pomfret", "rohu", "turkey", "duck", "seafood", "meat"}
ASK_WORDS = re.compile(r"\b(?:want|need|craving|crave|mood|feel like|feeling like|wish|chahiye|khana|what can i|what should i|what do i|have|eat|something|"
                       r"dishes?|options?|ideas?|suggest|show|give|batao|dikhao)\b")
PERMISSION = re.compile(r"\b(?:can|may|should|is|are|does|do|allowed|safe|good|bad|healthy|harmful|ok|okay)\b")
NEGATED = re.compile(r"\b(?:dont|don't|do not|doesnt|not|never|no|without|except|avoid|allergic|allergy|intolerant|nahi|nahin|mat)\b")
NOT_A_WISH = re.compile(r"\b(?:what can i|what should i|what do i|want|need|craving|crave|mood|feel like|feeling like|chahiye|khana)\b")


def ingredient_wish(cleaned: str, follow_up: bool = False) -> list[str]:
    """The meat or fish a message asks for ("i want chicken, what can i have"), or []. A permission question ("is chicken healthy",
    "can i eat fish") is not a wish: it keeps its own answer."""
    found = sorted({w for w in cleaned.split() if w in WISH_WORDS})
    if not found or NEGATED.search(cleaned):             # "i dont eat chicken", "no fish", "allergic to prawns" say what to keep out, not what is wanted
        return []
    if not (follow_up or ASK_WORDS.search(cleaned)):        # a follow-up to a search can be just "and prawns"
        return []
    if PERMISSION.search(cleaned) and not NOT_A_WISH.search(cleaned):
        return []
    return found


# "high protein", "low carb", "less fat", "most protein", "protein rich", "light": a wish for a lot or a little of something, with no number
HIGH_WORDS = r"(?:high|higher|highest|rich|richer|more|most|plenty of|lots of|good source of|full of|loaded with)"
LOW_WORDS = r"(?:low|lower|lowest|less|least|fewer|reduced|minimal)"
QUAL_NUTRIENT = r"(protein|proteins|carb|carbs|carbohydrate|carbohydrates|fat|fats|fibre|fiber|calories|calorie|kcal|energy)"
QUAL_FIELD = {**FIELD, "calories": "energy_kcal", "calorie": "energy_kcal", "kcal": "energy_kcal", "energy": "energy_kcal"}
QUAL_NOUN = {"energy_kcal": "calories", "protein_g": "protein", "carb_g": "carbohydrate", "fat_g": "fat", "fibre_g": "fibre"}
# a short message that is only a wish ("high protein food", "low carb dinner ideas") is a request too
QUAL_WANT = re.compile(r"\b(meal|meals|dish|dishes|food|foods|something|anything|recipe|recipes|options?|ideas?|suggest|suggestions?|recommend|list|which|what|"
                       r"breakfast|lunch|dinner|snacks?|i have to eat|to eat|can i have|can i eat|i want|i need|give me|show me|looking for)\b")
DRINK_WORDS = {"drink", "drinks", "juice", "juices", "beverage", "beverages", "shake", "shakes", "smoothie", "smoothies", "tea", "coffee", "milk", "lassi", "buttermilk",
               "soup", "soups"}


@dataclass
class Target:
    field: str
    value: float
    mode: str = "about"            # about | max | min | range | high | low  (high and low carry no number; a range runs from value to high)
    high: float | None = None


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
    for m in re.finditer(rf"{NUM}\s*(?:and|to|upto|up to)\s*{NUM}\s*(?:kcal|calories|calorie|cals|cal)\b|\b{NUM}\s+{NUM}\s*(?:kcal|calories|calorie|cals|cal)\b", cleaned):
        low, high = (float(x) for x in [g for g in m.groups() if g is not None][:2])
        if low < high and LIMITS["energy_kcal"][0] <= low and high <= LIMITS["energy_kcal"][1] and "energy_kcal" not in found:
            found["energy_kcal"] = Target("energy_kcal", low, "range", high)
    for m in re.finditer(rf"{NUM}\s*(?:and|to|upto|up to)\s*{NUM}\s*{G}\s*(?:of\s+)?{NUTRIENT}\b", cleaned):
        low, high, fld = float(m.group(1)), float(m.group(2)), FIELD[m.group(3)]
        if low < high and LIMITS[fld][0] <= low and high <= LIMITS[fld][1] and fld not in found:
            found[fld] = Target(fld, low, "range", high)

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


def parse_qualifiers(cleaned: str) -> list[Target]:
    """"high protein", "low carb", "less fat", "protein rich", "light": what the person wants a lot or a little of. No number is read."""
    found: dict[str, Target] = {}
    for m in re.finditer(rf"\b({HIGH_WORDS}|{LOW_WORDS})\s+(?:in\s+|on\s+)?{QUAL_NUTRIENT}\b", cleaned):
        fld = QUAL_FIELD[m.group(2)]
        word = m.group(1)
        found.setdefault(fld, Target(fld, 0.0, "low" if re.fullmatch(LOW_WORDS, word) else "high"))
    for m in re.finditer(rf"\b{QUAL_NUTRIENT}\s+(?:rich|heavy)\b", cleaned):
        found.setdefault(QUAL_FIELD[m.group(1)], Target(QUAL_FIELD[m.group(1)], 0.0, "high"))
    for m in re.finditer(r"\b(?:want|need|needs|require|craving|chahiye)\s+(?:some\s+|a\s+lot\s+of\s+)?(protein|proteins|fibre|fiber)\b", cleaned):
        found.setdefault(QUAL_FIELD[m.group(1)], Target(QUAL_FIELD[m.group(1)], 0.0, "high"))        # "i want protein": a wish for a lot of it
    if not found and re.search(r"\b(?:light|lighter)\b", cleaned):
        found["energy_kcal"] = Target("energy_kcal", 0.0, "low")
    return list(found.values())


def is_target_request(cleaned: str) -> list[Target]:
    """The amounts and wishes in a request for dishes ("a meal with 26 g protein", "high protein non veg food"), or []."""
    numeric = parse_targets(cleaned)
    if numeric and (WANT.search(cleaned) or parse_qualifiers(cleaned) or len(cleaned.split()) <= 6):
        return numeric + [t for t in parse_qualifiers(cleaned) if t.field not in {n.field for n in numeric}]
    wishes = parse_qualifiers(cleaned)
    if wishes and (QUAL_WANT.search(cleaned) or len(cleaned.split()) <= 6):
        return wishes
    return []


def wants_drinks(cleaned: str) -> bool:
    return bool(set(cleaned.split()) & DRINK_WORDS)


def _words(texts) -> set[str]:
    return {w for t in texts for w in re.findall(r"[a-z]+", str(t).lower())}


class TargetSearch:
    def __init__(self, dishes: DishLookup, allergy, index):
        self.dishes, self.allergy, self.index = dishes, allergy, index

    def _allowed(self, code: str, person, blocked: set[str], allergens: set[str], leave_out: set[str] = frozenset(),
                 only_nonveg: bool = False, include: set[str] = frozenset()) -> bool:
        names = [self.dishes.label(code), *self.dishes.ingredient_names(code)]
        words = _words(names)
        if leave_out and (words & leave_out or {w.rstrip("s") for w in words} & {w.rstrip("s") for w in leave_out}):
            return False
        if only_nonveg and not self.is_nonveg(code, words):
            return False
        if include and not ({w.rstrip("s") for w in words} & {w.rstrip("s") for w in include}):
            return False                                   # the dish must name one of the meats or fish asked for
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

    def is_nonveg(self, code: str, words: set[str] | None = None) -> bool:
        """Meat, fish or egg in the dish's name or ingredients, or a non-veg category in the library."""
        if words is None:
            words = _words([self.dishes.label(code), *self.dishes.ingredient_names(code)])
        rec = self.dishes.library.records.get(self.dishes.by_code.get(code, ""), {})
        return bool(words & (MEAT_FISH | EGG)) or "nonveg" in str(rec.get("category", "")).replace("_", "").lower()

    def _not_a_meal(self, code: str, result) -> bool:
        """A drink, a spoonful or an infant food: left out of a request for dishes unless drinks were asked for."""
        rules = self.dishes.rules.get("meal_search", {})
        unit = result.serving_text.partition(" ")[2].lower()
        category = str(self.dishes.library.records.get(self.dishes.by_code.get(code, ""), {}).get("category", "")).lower()
        return unit in set(rules.get("exclude_units", [])) or category in set(rules.get("exclude_categories", []))

    def _passes(self, target: Target, ps: dict) -> bool:
        """A wish ("high protein") is met when the dish passes the line the rules draw for it (dish_rules.json, qualifiers)."""
        rule = (self.dishes.rules.get("qualifiers", {}).get(target.field) or {}).get(target.mode) or {}
        value, energy = ps.get(target.field), ps.get("energy_kcal") or 0.0
        if value is None:
            return False
        kcal_per_g = {"protein_g": 4.0, "carb_g": 4.0, "fat_g": 9.0}.get(target.field)
        share = value * kcal_per_g / energy if kcal_per_g and energy > 0 else None
        checks = [("min_value", value >= rule.get("min_value", float("-inf"))), ("max_value", value <= rule.get("max_value", float("inf"))),
                  ("min_energy", energy >= rule.get("min_energy", 0)),
                  ("min_energy_share", share is not None and share >= rule.get("min_energy_share", 0) if "min_energy_share" in rule else True),
                  ("max_energy_share", share is not None and share <= rule.get("max_energy_share", 1) if "max_energy_share" in rule else True)]
        return bool(rule) and all(ok for _name, ok in checks)

    def search(self, targets: list[Target], person, blocked: set[str], limit: int = 3, leave_out=(), skip_codes=(), drinks: bool = False,
               only_nonveg: bool = False, include=(), relax: bool = False) -> tuple[list[Match], bool]:
        """(best matches, whether they are within the normal tolerance). Dishes with no clear serving or only an estimate are left out."""
        rules = self.dishes.rules.get("range_percent", {})
        tight = float(rules.get("default", 15)) / 100.0
        wide = tight * 2
        allergens: set[str] = set(person.allergies or []) if person is not None else set()
        if person is None or person.allergies is None or person.unmapped_allergies:
            allergens |= set(self.allergy.map["major"])          # allergies unknown or not understood: stay clear of every common one
        leave = {str(w).lower() for w in leave_out}
        want = {str(w).lower() for w in include}
        skip = set(skip_codes)
        scored: list[Match] = []
        for code in self.dishes.by_code:
            if code in skip:
                continue
            result = self.dishes.values(code)
            if result.status != FOUND or result.estimate or result.per_serving is None or not result.serving_text:
                continue
            ps = result.per_serving
            if not drinks and self._not_a_meal(code, result):
                continue
            errors = []
            for t in targets:
                v = ps.get(t.field)
                if v is None:
                    errors = None
                    break
                if t.mode in {"high", "low"}:
                    errors.append(0.0 if relax or self._passes(t, ps) else 9.0)       # relax: rank by the wish, whether or not it passes the line
                elif t.mode == "range" and t.high is not None:
                    errors.append(0.0 if t.value <= v <= t.high else (t.value - v) / t.value if v < t.value else (v - t.high) / t.high)
                elif t.mode == "max":
                    errors.append(0.0 if v <= t.value else (v - t.value) / t.value)
                elif t.mode == "min":
                    errors.append(0.0 if v >= t.value else (t.value - v) / t.value)
                else:
                    errors.append(abs(v - t.value) / t.value)
            if errors is None:
                continue
            scored.append(Match(code, self.dishes.label(code), result, max(errors, default=0.0), {t.field: ps[t.field] for t in targets}))

        def rank(m: Match) -> tuple:                                      # for wishes: the most (or least) of the first nutrient asked for
            for t in targets:
                if t.mode in {"high", "low"}:
                    return (-m.values[t.field] if t.mode == "high" else m.values[t.field],)
            return (m.result.per_serving.get("energy_kcal") or 0.0,) if not targets else (0,)      # no wish for a nutrient: the lightest first
        scored.sort(key=lambda m: (m.error, rank(m), m.name))
        for tol in (tight, wide):
            picked: list[Match] = []
            seen: set[str] = set()
            for m in scored:
                if m.error > tol:
                    break
                key = m.name.split(" (")[0].lower()
                if key in seen or not self._allowed(m.code, person, blocked, allergens, leave, only_nonveg, want):
                    continue
                seen.add(key)
                picked.append(m)
                if len(picked) >= limit:
                    break
            if picked:
                return picked, tol == tight
        return [], False
