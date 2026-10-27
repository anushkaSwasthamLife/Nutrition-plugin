"""The Dixit programme's recipe book (Healthful Diet, ADORE Trust 2021) as recipes the pipeline can offer (plugin step 5).

The book file stays outside this repository (it is for private circulation and an unverified transcription), like the INDB files:
`DIXIT_BOOK_RECIPES` points at it, and without it the book is simply off. What the repository holds is the import rule set,
`data/book_ingredient_map.json`: for every ingredient line in the book, which foods it is and how many grams.

Every dish becomes a recipe in the collection's own shape, so the usual flow applies unchanged: the doctor pack and the patient's
allergies, diet and limits filter it, USDA records give the numbers (never the book's printed values, which disagree with their
own macros for nine recipes), the gate checks it, and a swap or a scale tailors it. Spice blends, chutneys, sauces and other
components are not offered as dishes. A dish whose amounts cannot be worked out is left out and the reason is reported.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_ID = "healthful_diet_book"
TEST_STATUSES = {"TEST ONLY", "STUB", "PARTIAL", "PROPOSED", "DRAFT"}
UNIT_NAMES = {"teaspoon": "teaspoon", "teaspoons": "teaspoon", "spoon": "teaspoon", "tablespoon": "tablespoon",
              "tablespoons": "tablespoon", "count": "count", "pieces": "piece", "piece": "piece", "clove": "clove",
              "inch": "inch", "bunch": "bunch", "stalks": "stalk", "sprigs": "stalk", "lemon": "lemon", "packet": "packet",
              "vati": "vati", "g": "g", "kg": "kg", "ml": "ml", "pinch": "pinch"}
FOOD_UNIT_KEYS = {"count": ("count", "piece", "medium", "clove"), "piece": ("piece", "count", "medium"), "clove": ("clove",),
                  "inch": ("inch",), "bunch": ("bunch",), "stalk": ("stalk",), "lemon": ("lemon",), "packet": ("packet",)}
SHOWN_UNITS = {"vati", "teaspoon", "tablespoon", "clove", "inch", "bunch", "stalk", "lemon", "packet"}
PLURALS = {"teaspoon": "teaspoons", "tablespoon": "tablespoons", "clove": "cloves", "bunch": "bunches", "stalk": "stalks",
           "lemon": "lemons", "packet": "packets"}
FRACTIONS = {0.25: "1/4", 0.33: "1/3", 0.5: "1/2", 0.67: "2/3", 0.75: "3/4"}
AROMATICS = {"FOOD-ONION", "FOOD-TOMATO", "FOOD-GARLIC", "FOOD-GINGER", "FOOD-GREEN-CHILLI", "FOOD-LEMON-JUICE", "FOOD-CORIANDER-LEAVES"}


@dataclass
class BookImport:
    recipes: list = field(default_factory=list)       # dishes, in the recipe collection's shape
    components: list = field(default_factory=list)    # [{"id", "name", "why"}]: spice blends, chutneys... never offered alone
    skipped: list = field(default_factory=list)       # [{"id", "name", "reasons"}]: dishes whose amounts could not be worked out
    title: str = ""
    status: str = ""

    def summary(self) -> dict:
        return {"title": self.title, "status": self.status, "dishes": len(self.recipes), "components": len(self.components),
                "skipped": [{"id": s["id"], "name": s["name"], "reasons": s["reasons"][:3]} for s in self.skipped]}


def load_book(path: Path | None) -> dict | None:
    """The book file, or None when it is not configured or not there (the book is then off)."""
    if path is None or not Path(path).is_file():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("recipes"), list):
        raise ValueError(f"{path}: not a Healthful Diet recipes file")
    return data


def _quantity(value) -> float | None:
    """2, 0.5, "7-8" (the middle), "0.75-1"; None when there is no number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", value)]
        if numbers:
            return sum(numbers[:2]) / len(numbers[:2])
    return None


def _amount_text(qty: float, unit: str) -> str:
    """The book's amount as a cook reads it: "1/2 vati", "1 1/2 teaspoons", "2 bunches"."""
    whole, rest = int(qty), round(qty - int(qty), 2)
    part = FRACTIONS.get(rest, f"{rest:g}" if rest else "")
    number = " ".join(x for x in (str(whole) if whole else "", part) if x) if part in FRACTIONS.values() or not rest else f"{qty:g}"
    return f"{number} {PLURALS.get(unit, unit) if qty > 1 else unit}"


WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _yield(total_yield, serving_size) -> dict | None:
    """The book's yield as {"count", "unit", "per_serving"}: "5 vati" / "1 vati" -> 5 vati, 1 a serving; "9-10 kababs" / "2 kababs"
    -> 9.5 kabab, 2 a serving. None when the yield has no number and a unit; per_serving None when the two units differ."""
    def parse(text):
        text = str(text or "").lower().replace("no.", "").strip()
        m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s+([a-z]+)", text)
        if not m:
            return None
        low, high, unit = float(m.group(1)), float(m.group(2) or m.group(1)), m.group(3)
        unit = unit[:-2] if unit.endswith("es") and unit[:-2] in {"vati"} else unit.rstrip("s") if unit != "vati" else unit
        return (low + high) / 2, unit
    made, one = parse(total_yield), parse(serving_size)
    if made is None or made[1] in {"gm", "g", "kg", "tablespoon", "glasse", "glass"}:
        return None
    return {"count": made[0], "unit": made[1], "per_serving": one[0] if one and one[1] == made[1] else None}


def _unit_grams(unit: str, entry: dict, food: dict) -> float | None:
    per = entry.get("per") or {}
    if unit in per:
        return float(per[unit])
    if unit in {"count", "piece"} and "count" in per:
        return float(per["count"])
    weights = food.get("unit_weights_g") or {}
    if unit == "teaspoon":
        return weights.get("teaspoon") or (weights["tablespoon"] / 3 if weights.get("tablespoon") else None)
    if unit == "tablespoon":
        return weights.get("tablespoon") or (weights["teaspoon"] * 3 if weights.get("teaspoon") else None)
    for key in FOOD_UNIT_KEYS.get(unit, ()):
        if weights.get(key):
            return float(weights[key])
    return None


def convert(book: dict, mapping: dict, foods: dict) -> BookImport:
    out = BookImport(title=(book.get("_about") or {}).get("source", {}).get("title", ""), status=mapping.get("status", ""))
    items, units, no_amount = mapping["items"], mapping["units"], set(mapping["no_amount_units"])
    signed = str(mapping.get("status", "")).upper() not in TEST_STATUSES
    meta = mapping.get("book") or {}
    book_id, short = meta.get("id", "book"), meta.get("short_title") or meta.get("title", "the book")
    for raw in book["recipes"]:
        rid, name = raw["id"], raw["name"]
        kind = mapping["kind_overrides"].get(rid, mapping["dish_categories"].get(raw.get("category")))
        if kind is None:
            why = mapping.get("kind_override_notes", {}).get(rid) or f"a {str(raw.get('category', '')).replace('_', ' ')}, not a dish"
            out.components.append({"id": rid, "name": name, "why": why})
            continue
        grams: dict[str, float] = {}
        shown: dict[str, str] = {}
        assumed, trace, optional, reasons = [], [], [], []
        for ing in raw["ingredients"]:
            text = ing["item"]
            entry = items.get(text)
            if entry is None:
                reasons.append(f"no mapping for '{text}'")
                continue
            if entry.get("skip"):
                continue
            if entry.get("trace"):
                trace.append(text)
                continue
            if entry.get("exclude"):
                reasons.append(f"'{text}': {entry['exclude']}")
                continue
            unit_text = str(ing.get("unit") or "").strip().lower()
            unit = UNIT_NAMES.get(unit_text)
            qty = _quantity(ing.get("quantity"))
            parts = entry.get("split") or [entry.get("food")]
            if entry.get("optional") or (unit_text == "optional" and "default_g" not in entry):
                optional.append(text)
                continue
            if unit_text in no_amount or unit is None and qty is None:
                if "default_g" not in entry:
                    reasons.append(f"'{text}' has no amount ('{unit_text}') and no assumed amount")
                    continue
                total = float(entry["default_g"])
                assumed.append({"text": text, "grams": total, "book": unit_text or "no amount", "foods": parts})
            elif unit in units and not isinstance(units[unit], str):
                factor = float((entry.get("per") or {}).get(unit, units[unit]))
                total = (qty if qty is not None else 1.0) * factor
            elif unit is not None:
                each = _unit_grams(unit, entry, foods.get(parts[0], {}))
                if each is None:
                    reasons.append(f"'{text}': no gram weight for one {unit}")
                    continue
                total = (qty if qty is not None else 1.0) * each
            else:
                reasons.append(f"'{text}': unknown unit '{unit_text}'")
                continue
            for fid in parts:
                if fid not in foods:
                    reasons.append(f"'{text}': unknown food {fid}")
                    continue
                grams[fid] = grams.get(fid, 0.0) + total / len(parts)
                if len(parts) == 1 and unit in SHOWN_UNITS and qty is not None and fid not in shown:
                    raw_qty = ing.get("quantity")
                    shown[fid] = (f"{raw_qty} {PLURALS.get(unit, unit)}" if isinstance(raw_qty, str)       # a range as the book prints it
                                  else _amount_text(qty, unit))
                elif fid in shown and len(parts) == 1:
                    shown[fid] = ""                     # the same food twice: only the grams are shown
        grams = {f: g for f, g in grams.items() if g > 0}
        if not reasons and not grams:
            reasons.append("no ingredient with an amount")
        if len(raw.get("steps") or []) < 2:
            reasons.append("fewer than two steps in the book (every offered recipe needs at least two)")
        if reasons:
            out.skipped.append({"id": rid, "name": name, "reasons": reasons})
            continue
        categories = {foods[f]["category"] for f in grams}
        diet = "non_vegetarian" if categories & {"meat", "fish"} else "egg" if "egg" in categories else "vegetarian"
        main = sorted((f for f in grams if foods[f]["category"] not in {"pantry", "sweet"} and f not in AROMATICS),
                      key=lambda f: -grams[f])[:3] or sorted(grams, key=lambda f: -grams[f])[:1]
        out.recipes.append({
            "recipe_id": rid, "name": name,
            "aliases": sorted({name, *[str(v) for v in (raw.get("local_names") or {}).values() if v]}),
            "kind": kind, "main_ingredients": main, "diet": diet,
            "makes": f"the whole dish; the book's yield is {raw.get('total_yield') or 'not stated'}",
            "ingredients": [{"food": f, "grams": round(g, 1), "unit": shown.get(f, "")} for f, g in grams.items()],
            "steps": list(raw["steps"]), "why_it_fits": "", "meal_place": "home_meal", "source": meta.get("source_id", SOURCE_ID),
            "book": {"id": rid, "page": raw.get("book_page"), "title": short, "signed": signed, "source_book": book_id,
                     "citation": meta.get("citation", short)},
            "assumed": assumed, "trace_items": trace, "optional_items": optional,
            "printed_nutrition": raw.get("nutrition_per_serving"),
            "yield": _yield(raw.get("total_yield"), raw.get("serving_size")),
        })
    return out
