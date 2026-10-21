"""Foods that are not in the approved food list (stage 4c): read what is being asked, find USDA facts for it, never guess.

"Protein in 50 g chicken", "calories in apple", "can I have ice cream", "is coffee ok": the food list is small (dietitian-approved), the
USDA library is large. For a lookup the facts come from the best USDA record (scaled to the grams asked for, a plain sum done in code and
locked as a fact); for "can I eat X" the chat says that it cannot judge X for the programme yet, passes X to the dietitian, and gives the
facts as information only. A food that does not suit the person's diet or allergies is never given a number.
"""
from __future__ import annotations

import re

from .dish_lookup import SCAFFOLD
from .facts import FIELD_LABELS, Fact, _num
from .semantics import NON_FOOD_WORDS
from .textnorm import clean

NUTRIENT_WORDS = {"calorie", "calories", "kcal", "energy", "carb", "carbs", "carbohydrate", "carbohydrates", "protein", "proteins", "fat", "fats", "fibre",
                  "fiber", "nutrition", "nutrients", "nutrient", "sugar", "sugars", "sodium", "cholesterol", "vitamin", "vitamins", "mineral", "minerals"}
UNIT_WORDS = {"g", "gm", "gms", "gram", "grams", "kg", "ml", "litre", "liter", "l", "cup", "cups", "bowl", "bowls", "katori", "plate", "plates", "glass", "glasses",
              "spoon", "spoons", "tablespoon", "teaspoon", "tbsp", "tsp", "piece", "pieces", "slice", "slices", "small", "medium", "large", "big", "tiny", "half",
              "one", "two", "three", "four", "five", "six", "ten", "dozen", "per", "nos", "no", "serving", "servings", "handful"}
GRAMS = re.compile(r"(?<![\w.])(\d{1,4}(?:\.\d+)?)\s*(?:g|gm|gms|gram|grams)\b(?!\s*(?:of\s+)?(?:protein|carbs?|carbohydrates?|fat|fats|fibre|fiber)\b)")
MEASURES = {"cup", "cups", "bowl", "bowls", "katori", "plate", "plates", "glass", "glasses", "spoon", "spoons", "tablespoon", "teaspoon", "tbsp", "tsp", "handful"}
# "can i eat X", "is X ok", "should i have X": what the person asks about when X is not a food on our list
SUBJECT_PATTERNS = [
    re.compile(r"\b(?:can|may|could|should|shall) (?:i|we) (?:eat|have|take|drink|try|add|include|use|get)\s+(?P<x>.+)$"),
    re.compile(r"\bis it (?:ok|okay|fine|safe|alright|allowed) (?:to|for me to) (?:eat|have|drink|take)\s+(?P<x>.+)$"),
    re.compile(r"\b(?:is|are)\s+(?P<x>.+?)\s+(?:ok|okay|allowed|permitted|good|bad|fine|safe|healthy|unhealthy|alright|harmful)(?:\s+(?:for me|to eat|to have|to drink))?$"),
    re.compile(r"\bam i allowed (?:to )?(?:eat |have |drink )?(?P<x>.+)$"),
    re.compile(r"\bis (?P<x>.+?)\s+allowed$"),
]
CATEGORY_WORDS = {"fruit", "fruits", "vegetable", "vegetables", "veggies", "food", "foods", "snacks", "snack", "sweets", "sweet", "drinks", "drink", "anything",
                  "everything", "something", "meat", "nonveg", "dairy", "grains", "cereals", "pulses", "nuts", "dessert", "desserts", "junk"}
TIME_TALK = re.compile(r"\b\d{1,2}\s*(?:am|pm)\b|\bo clock\b|\b(?:after|before|at|till|until|by)\s+\d|\b(?:late night|midnight|every day|daily|twice|thrice)\b")
UNWANTED = {"cake", "cakes", "candy", "candies", "infant", "formula", "baby", "babyfood", "toddler", "bubble", "supplement", "pet", "dog", "cat"}
DISH_NOISE = {"reduce", "lose", "gain", "control", "manage", "improve", "increase", "avoid", "burn", "build", "cut", "stop", "start", "make", "do", "get", "feel", "am", "pm", "oclock", "clock", "hour", "hours", "minutes", "min", "late", "early", "time", "times", "a", "an", "the", "some", "any", "my", "your", "this", "that", "these", "those", "of", "for", "me", "us", "to", "in", "if", "i", "am", "is", "are",
              "daily", "everyday", "regularly", "often", "sometimes", "today", "tonight", "tomorrow", "now", "later", "ever", "also", "too", "please", "plz", "pls",
              "diabetic", "diabetes", "sugar", "patient", "weight", "loss", "while", "during", "after", "before", "at", "night", "morning", "evening", "lunch", "dinner",
              "breakfast", "bp", "pcos", "thyroid", "pregnant", "pregnancy"}


def grams_in(cleaned: str) -> float | None:
    """The grams a message names ("50 g chicken"); None when there are none or the number belongs to a nutrient ("26 g protein")."""
    m = GRAMS.search(cleaned)
    if not m:
        return None
    value = float(m.group(1))
    return value if 0 < value <= 5000 else None


def measure_in(cleaned: str) -> str | None:
    for word in cleaned.split():
        if word in MEASURES:
            return word
    return None


def _generic_words() -> set:
    """Words that only describe a dish ("dry", "vegetarian", "quick"): from the dish rules, never a food to look up."""
    global _GENERIC
    if _GENERIC is None:
        import json
        from .config import DATA_DIR
        try:
            _GENERIC = set(json.loads((DATA_DIR / "dish_rules.json").read_text(encoding="utf-8")).get("generic_words", []))
        except (OSError, ValueError):
            _GENERIC = set()
    return _GENERIC


_GENERIC: set | None = None


def food_phrase(cleaned: str, extra_stop: set | None = None) -> str:
    """The words of a message that can name a food: what is left once the question, numbers, units and describing words are taken out."""
    stops = SCAFFOLD | UNIT_WORDS | NUTRIENT_WORDS | NON_FOOD_WORDS | DISH_NOISE | _generic_words() | (extra_stop or set())
    words = [w.strip("'") for w in cleaned.split()]
    kept = [w for w in words if w and w not in stops and not re.fullmatch(r"[\d.]+(?:g|gm|kg|ml|kcal)?", w) and len(w) > 1]
    return " ".join(kept[:4]) if kept else ""


def subject_of(cleaned: str) -> str:
    """The food a "can I eat ...?" / "is ... ok?" question is about, or ""."""
    if TIME_TALK.search(cleaned):
        return ""                                            # "is it ok to eat after 8 pm" is about timing, not about a food
    for pattern in SUBJECT_PATTERNS:
        m = pattern.search(cleaned)
        if m:
            rest = m.group("x")
            if re.match(r"(?:to|so|if|when|what|which|how|why|that|because)", rest):
                return ""                                      # "should i eat to reduce it": a purpose, not a food
            phrase = food_phrase(rest)
            return "" if (not phrase or set(phrase.split()) <= CATEGORY_WORDS) else phrase      # "can i eat fruits" is a question about a kind of food
    return ""


def scaled_fact(fact: Fact, grams: float) -> Fact:
    """The same USDA fact for the grams asked for: value x grams / 100. Only a per-100 g fact is scaled; anything else is returned as it is."""
    if fact.serving != "per 100 g" or fact.value is None or fact.field not in FIELD_LABELS:
        return fact
    unit, label = FIELD_LABELS[fact.field]
    value = fact.value * grams / 100.0
    shown = _num(value, unit)
    amount = f"{shown} calories" if unit == "kcal" else f"{shown} g of {label}"
    serving = f"per {grams:g} g"
    return Fact(fact.record_id, fact.field, float(value), f"about {amount} {serving}", serving, fact.source, fact.food_name, amount)


def variants(phrase: str) -> list[str]:
    """The phrase and its simple plural / singular, so "apple" finds "Apples"."""
    words = phrase.split()
    if not words:
        return []
    last = words[-1]
    out = [phrase]
    if last.endswith("s") and len(last) > 3:
        out.append(" ".join(words[:-1] + [last[:-1]]))
    else:
        out += [" ".join(words[:-1] + [last + "s"]), " ".join(words[:-1] + [last + "es"])]
    return out


PLAIN_WORDS = {"raw", "whole", "plain", "fresh", "cooked", "boiled", "roasted"}


def usda_hits(search, library, phrase: str, field: str, make_fact, limit: int = 3, name_ok=None) -> list[Fact]:
    """Facts for the best USDA records that match a phrase (not Indian dishes), best first, one per different name."""
    seen_names, seen_ids, out = set(), set(), []
    candidates = []
    for query in variants(clean(phrase)):
        for hit in search.search(query, limit=800, usda_only=True):
            if hit["record_id"] not in seen_ids:
                seen_ids.add(hit["record_id"])
                candidates.append(hit)
    asked_first = {v.split()[0] for v in variants(clean(phrase)) if v}
    if name_ok is not None:                                  # diet and allergies: a record whose name holds a food the person cannot have is never shown
        candidates = [h for h in candidates if name_ok(h["food_name"])]

    def order(h):
        name = clean(h["food_name"])
        words = name.split()
        # USDA writes the food first ("Milk, whole", "Oats, ...", "Apples, raw"): a name that starts with the asked word comes before one that merely
        # contains it, and the plain form (raw, whole) before a prepared one
        return (bool(words) and words[0] in asked_first, bool(set(words) & PLAIN_WORDS), not h.get("prepared"), h["score"])
    candidates.sort(key=order, reverse=True)
    asked = set(clean(phrase).split())
    candidates = [h for h in candidates if not (set(clean(h["food_name"]).split()) & UNWANTED - asked)]       # not the cake, the candy or the baby food
    for hit in candidates:
        fact = make_fact(library, hit["record_id"], field)
        name = clean(hit["food_name"])
        if fact and name not in seen_names:
            seen_names.add(name)
            out.append(fact)
        if len(out) >= limit:
            break
    return out
