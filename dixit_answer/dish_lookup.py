"""Dish lookup (INDB plan, phase 3): from the words a person used to the nutrition of one dish, with the path it came from.

Path A  the dish is in the Swastham library: its stored values are used as they are.
Path B  the dish is an INDB recipe that the library lacks: calculated from its ingredients (indb_calc), only when every ingredient
        has a value, always given as an estimate range.
Neither  not found, ambiguous, incomplete or implausible: no number is given and the caller says so.

Names are matched whole word by whole word, never by substring. Two different dishes that match equally well are never separated by
a quiet choice: the lookup returns both so the person can be asked. Nothing here asks a language model for a number. In production
the INDB and Indian sources are off (licence pending), so every dish is "not found".
"""
from __future__ import annotations

import csv
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .indb_calc import NUTRIENTS, WITH_PROXIES, Sources, calculate
from .loader import FLAG_MACRO_MISMATCH, FLAG_SERVING_UNCLEAR, Library
from .textnorm import clean

FOUND, AMBIGUOUS, INCOMPLETE, IMPLAUSIBLE, NO_SERVING, NOT_FOUND = (
    "found", "ambiguous", "incomplete", "implausible", "no_reliable_serving", "not_found")


@dataclass
class IndbRecipe:
    code: str
    name: str
    rows: list
    servings: float | None = None        # how many servings the recipe makes
    serving_size: float | None = None    # units in one serving
    serving_unit: str = ""


@dataclass
class DishResult:
    status: str
    query: str
    code: str | None = None
    name: str | None = None
    path: str | None = None              # "A" library, "B" calculated
    estimate: bool = False
    per_serving: dict | None = None      # NUTRIENTS -> value
    per_100g: dict | None = None
    serving_text: str = ""
    range_kcal: tuple | None = None      # (low, high) per serving, set for every estimate
    constraint: str | None = None        # the record's own usage constraint: the wording must respect it
    candidates: list = field(default_factory=list)
    unvalued: list = field(default_factory=list)
    lineage: dict = field(default_factory=dict)
    note: str = ""
    range_percent: float | None = None   # how wide the estimate range is, as a percentage of the value

    @property
    def has_numbers(self) -> bool:
        return self.status == FOUND and (self.per_serving is not None or self.per_100g is not None)


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")) or value < 0:
        return None
    return float(value)


def _from_record(block) -> dict | None:
    """The five compared nutrients from a library per-serving or per-100 g block; None when energy is missing."""
    if not isinstance(block, dict):
        return None
    out = {"energy_kcal": _num(block.get("energy_kcal")), "protein_g": _num(block.get("protein_g")), "fat_g": _num(block.get("fat_g")),
           "carb_g": _num(block.get("net_carbohydrate_g")), "fibre_g": _num(block.get("fiber_g"))}
    return out if out["energy_kcal"] is not None else None


class DishLookup:
    def __init__(self, library: Library, alias_rows: list[dict] | None, recipes: dict[str, IndbRecipe] | None,
                 sources: Sources | None, rules: dict, spelling_map: dict | None = None):
        self.library = library
        self.rules = rules
        self.spelling = spelling_map or {}
        self.recipes = recipes or {}
        self.sources = sources
        self.by_code: dict[str, str] = {}                    # dish code -> library record id
        for rid, rec in library.records.items():
            if rec.get("source_collection") == "SWASTHAM_INDIAN_MEAL_LIBRARY" and rec.get("source_record_id"):
                self.by_code[str(rec["source_record_id"])] = rid
        self.names: dict[str, set[str]] = {}                 # dish code -> cleaned names
        self.exact: dict[str, set[str]] = {}                 # cleaned alias -> dish codes

        def add(code: str, name: str) -> None:
            cleaned = clean(name, self.spelling)
            if cleaned:
                self.names.setdefault(code, set()).add(cleaned)
                self.exact.setdefault(cleaned, set()).add(code)

        for code, rid in self.by_code.items():
            rec = library.records[rid]
            for name in [rec.get("food_name", ""), *(rec.get("aliases") or [])]:
                add(code, str(name))
        for code, recipe in self.recipes.items():
            add(code, recipe.name)
        for row in alias_rows or []:
            code = str(row.get("target ID", "")).strip()
            if row.get("kind") == "dish" and code and (code in self.by_code or code in self.recipes):
                add(code, str(row.get("alias (normalised)", "")))

    # ------------------------------------------------------------------ finding the dish
    def candidates(self, query: str) -> tuple[list[str], str]:
        """(dish codes that match best, cleaned query). More than one code means the caller must ask which."""
        q = clean(query, self.spelling)
        if not q:
            return [], q
        hit = self.exact.get(q)
        if hit:
            return sorted(hit), q
        words = set(q.split())
        best: list[tuple[int, str]] = []
        for code, names in self.names.items():
            extras = [len(set(n.split()) - words) for n in names if words <= set(n.split())]
            if extras:
                best.append((min(extras), code))
        if not best:
            return [], q
        low = min(extra for extra, _ in best)
        return sorted(code for extra, code in best if extra == low), q

    def ingredient_names(self, code: str) -> list[str]:
        """Every ingredient name INDB lists for a dish, for allergy checks. Empty when INDB has no recipe rows for it."""
        recipe = self.recipes.get(code)
        if recipe is None:
            return []
        return [str(n) for row in recipe.rows for n in (row.get("food_name"), row.get("ingredient_name_org")) if n]

    def label(self, code: str) -> str:
        if code in self.by_code:
            return str(self.library.records[self.by_code[code]].get("food_name", code))
        return self.recipes[code].name if code in self.recipes else code

    def resolve(self, query: str) -> DishResult:
        codes, q = self.candidates(query if isinstance(query, str) else "")
        if not codes:
            return DishResult(NOT_FOUND, str(query)[:80])
        if len(codes) > 1:
            limit = int(self.rules.get("max_candidates_asked", 4))
            return DishResult(AMBIGUOUS, q, candidates=[{"code": c, "name": self.label(c)} for c in codes[:limit]],
                              note=f"{len(codes)} different dishes match; ask which one")
        return self.values(codes[0], q)

    # ------------------------------------------------------------------ the numbers
    def values(self, code: str, query: str = "") -> DishResult:
        if code in self.by_code:
            result = self._path_a(code, query)
            if result.status != IMPLAUSIBLE or code not in self.recipes:
                return result
        if code in self.recipes:
            return self._path_b(code, query)
        return DishResult(NOT_FOUND, query, code=code)

    def _plausible(self, per_100g: dict | None, per_serving: dict | None, calculated: bool = False, serving_text: str = "") -> bool:
        cfg = self.rules.get("plausibility", {})
        if per_100g and per_100g["energy_kcal"] > cfg.get("max_kcal_per_100g", 900):
            return False
        if not per_serving or per_serving["energy_kcal"] is None:
            return True
        kcal = per_serving["energy_kcal"]
        if kcal > cfg.get("max_kcal_per_serving_calculated" if calculated else "max_kcal_per_serving", 1500):
            return False
        quantity, _, unit = serving_text.partition(" ")
        if unit and unit not in cfg.get("vessel_units", []):               # a serving counted in pieces: judge it per piece
            try:
                count = float(quantity)
            except ValueError:
                count = 1.0
            if count > 0 and kcal / count > cfg.get("max_kcal_per_count_unit", 400):
                return False
        return True

    def _range(self, kcal: float, meat_fish: bool) -> tuple[float, float]:
        pct = self.rules.get("range_percent", {}).get("meat_fish" if meat_fish else "default", 15) / 100.0
        return kcal * (1 - pct), kcal * (1 + pct)

    def _path_a(self, code: str, query: str) -> DishResult:
        rid = self.by_code[code]
        rec = self.library.records[rid]
        flags = self.library.flags.get(rid, set())
        quality = rec.get("data_quality") or {}
        base = dict(query=query, code=code, name=rec.get("food_name"), path="A", constraint=quality.get("usage_constraint"),
                    lineage={"record_id": rid, "source": rec.get("source_dataset"), "energy_basis": quality.get("energy_basis")})
        if FLAG_MACRO_MISMATCH in flags:
            return DishResult(IMPLAUSIBLE, note="the stored values do not add up (fat, carbohydrate and protein disagree with energy)", **base)
        per_serving, per_100g = _from_record(rec.get("nutrients_per_serving")), _from_record(rec.get("nutrients_per_100g"))
        serving = rec.get("serving") or {}
        text = f"{serving.get('quantity'):g} {serving.get('unit')}" if _num(serving.get("quantity")) and serving.get("unit") else ""
        if per_serving is None and per_100g is None:
            return DishResult(NOT_FOUND, note="the record has no energy value", **base)
        if FLAG_SERVING_UNCLEAR in flags:
            if per_100g is None:
                return DishResult(NO_SERVING, note="the serving size of this record is unclear and there is no per-100 g value", **base)
            per_serving, text = None, ""
        if not self._plausible(per_100g, per_serving, False, text):
            return DishResult(IMPLAUSIBLE, note="the stored energy is higher than a dish can be", **base)
        result = DishResult(FOUND, per_serving=per_serving, per_100g=per_100g, serving_text=text, **base)
        if quality.get("requires_approximation_disclosure"):
            rng = rec.get("energy_estimate_range_per_serving") or {}
            low, high = _num(rng.get("low_kcal")), _num(rng.get("high_kcal"))
            if (low is None or high is None) and per_serving:
                low, high = self._range(per_serving["energy_kcal"], False)
            if low is None or high is None or per_serving is None:
                return DishResult(NO_SERVING, note="the energy of this dish is only known approximately and no range is stored", **base)
            result.estimate, result.range_kcal = True, (low, high)
            result.per_serving = {**per_serving, "energy_kcal": None}       # a range, never a single energy figure
        return result

    def _path_b(self, code: str, query: str) -> DishResult:
        recipe = self.recipes[code]
        base = dict(query=query, code=code, name=recipe.name, path="B", estimate=True,
                    lineage={"source": "INDB recipe, calculated from ingredients"})
        cfg = self.rules.get("calculated_dishes", {})
        if not cfg.get("enabled") or self.sources is None:
            return DishResult(NOT_FOUND, note="calculating dishes from ingredients is switched off", **base)
        result = calculate(recipe.rows, self.sources)
        coverage = result.coverage
        if not result.complete or coverage is None or coverage < cfg.get("min_coverage", 1.0):
            return DishResult(INCOMPLETE, unvalued=[n for n, _g, _w in result.unvalued] + [n for n, _u in result.unweighed],
                              note="an ingredient has no usable value, so no number is given", **base)
        per_100g = result.per_100g()
        if per_100g is None:
            return DishResult(INCOMPLETE, note="the recipe has no weight", **base)
        per_serving, text = None, ""
        if recipe.servings and recipe.servings > 0:
            per_serving = {n: result.totals[n] / recipe.servings for n in NUTRIENTS}
            text = f"{recipe.serving_size:g} {recipe.serving_unit}" if recipe.serving_size and recipe.serving_unit else "1 serving"
        if not self._plausible(per_100g, per_serving, True, text):
            return DishResult(IMPLAUSIBLE, note="the calculated energy is higher than a dish can be", **base)
        letters = set(self.rules.get("meat_fish_categories", []))
        meat_fish = any(str(c)[:1] in letters for c, _n, _g, _k in result.parts)
        kcal = per_serving["energy_kcal"] if per_serving else per_100g["energy_kcal"]
        pct = float(self.rules.get("range_percent", {}).get("meat_fish" if meat_fish else "default", 15))
        return DishResult(FOUND, per_serving=per_serving, per_100g=per_100g, serving_text=text, range_percent=pct,
                          range_kcal=self._range(kcal, meat_fish) if per_serving else None, **base)


# ---------------------------------------------------------------- the sentence for one nutrient
FIELD_WORDS = {"energy_kcal": ("kcal", ""), "carb_g": ("g", "carbohydrate"), "protein_g": ("g", "protein"), "fat_g": ("g", "fat"),
               "fibre_g": ("g", "fibre")}


@dataclass
class DishFact:
    text: str                 # "about 161 kcal per bowl"
    value: float | None       # None for a range
    serving: str
    estimate: bool
    low: float | None = None  # the range, rounded the way it is shown
    high: float | None = None


def _serving_words(result: DishResult) -> str:
    if result.per_serving is None:
        return "per 100 g"
    if not result.serving_text:
        return "per serving"
    quantity, _, unit_name = result.serving_text.partition(" ")
    return f"per {unit_name}" if quantity in {"1", "1.0"} and unit_name else f"per {result.serving_text}"


def _amount(value: float, unit: str, label: str, estimate: bool) -> str:
    if unit == "kcal":
        return f"{int(round(value / 5.0) * 5) if estimate else round(value):d}"
    return f"{round(value, 1):g}"


def fact_text(result: DishResult, field_name: str = "energy_kcal") -> DishFact | None:
    """The sentence for one nutrient of a found dish, or None when the dish has no number for it. Every estimate is a range."""
    if not result.has_numbers or field_name not in FIELD_WORDS:
        return None
    unit, label = FIELD_WORDS[field_name]
    block = result.per_serving if result.per_serving is not None else result.per_100g
    value = block.get(field_name) if block else None
    serving = _serving_words(result)
    pct = (result.range_percent if result.range_percent is not None else 15.0) / 100.0
    low = high = None
    if field_name == "energy_kcal" and result.range_kcal is not None and result.per_serving is not None:
        low, high = result.range_kcal
    elif value is None:
        return None
    elif result.path == "B":
        low, high = value * (1 - pct), value * (1 + pct)
    thing = "kcal" if unit == "kcal" else f"g {label}"
    if low is not None:
        shown_low, shown_high = _amount(low, unit, label, True), _amount(high, unit, label, True)
        return DishFact(f"about {shown_low}–{shown_high} {thing} {serving}, an estimate", None, serving, True,
                        float(shown_low), float(shown_high))
    shown_value = _amount(value, unit, label, False)
    return DishFact(f"about {shown_value} {thing} {serving}", float(shown_value), serving, False)


# ---------------------------------------------------------------- plain wording for a person who is not an expert
NUTRIENT_PHRASE = {"energy_kcal": "calories", "carb_g": "g of carbohydrate", "protein_g": "g of protein", "fat_g": "g of fat",
                   "fibre_g": "g of fibre"}


def _plural(unit: str) -> str:
    return unit if unit.endswith("s") else unit + "s"


def _number_word(value: float) -> str:
    return f"{value:g}"


def plain_sentence(result: DishResult, field_name: str, dish_name: str) -> str | None:
    """"A bowl of poha has about 161 calories." The same numbers as fact_text, in a sentence a person can read at a glance.

    Calories, not kcal; a household unit, not a serving code; a range is introduced with "roughly" and given as "from X to Y".
    """
    shown = fact_text(result, field_name)
    if shown is None:
        return None
    dish = (dish_name or result.name or "this dish").split(" (")[0].strip()
    dish = dish.lower()
    phrase = NUTRIENT_PHRASE[field_name]
    if shown.low is not None:
        amount = f"roughly {_number_word(shown.low)} to {_number_word(shown.high)} {phrase}"
    else:
        amount = f"about {_number_word(shown.value)} {phrase}"
    if result.per_serving is None:
        who = f"100 g of {dish}"
    elif not result.serving_text:
        who = f"One serving of {dish}"
    else:
        quantity, _, unit = result.serving_text.partition(" ")
        try:
            count = float(quantity)
        except ValueError:
            count = 1.0
        if not unit:
            who = f"One serving of {dish}"
        elif count == 1:
            who = f"{'An' if unit[:1].lower() in 'aeiou' else 'A'} {unit} of {dish}"
        else:
            who = f"One serving of {dish} ({_number_word(count)} {_plural(unit)})"
    return f"{who} has {amount}."


# ---------------------------------------------------------------- finding a dish inside a sentence
# Words that make up the question itself ("how many calories in a plate of ..."), never the dish.
SCAFFOLD = {"how", "many", "much", "what", "whats", "is", "are", "the", "in", "of", "a", "an", "calorie", "calories", "kcal", "carb",
            "carbs", "carbohydrate", "carbohydrates", "protein", "proteins", "fat", "fats", "fibre", "fiber", "nutrition", "nutrients",
            "nutrient", "energy", "per", "one", "1", "plate", "bowl", "cup", "katori", "piece", "pieces", "glass", "serving", "servings",
            "tell", "me", "about", "for", "give", "does", "do", "have", "has", "contain", "contains", "there", "any", "total", "approx",
            "approximately", "and", "or", "to", "can", "you", "please", "pls", "my", "i", "it", "this", "that", "show", "ka", "ki",
            "mein", "kitna", "kitni", "hai", "hota"}
MAX_SPAN_WORDS = 8


def find_in_text(lookup: "DishLookup", cleaned: str) -> tuple[str, DishResult] | None:
    """The dish a sentence is about: the longest run of words that names one, ignoring the words of the question itself."""
    words = cleaned.split()[:40]
    spans = [(i, i + length) for length in range(min(len(words), MAX_SPAN_WORDS), 0, -1) for i in range(0, len(words) - length + 1)]
    spans = [(i, j) for i, j in spans if any(w not in SCAFFOLD for w in words[i:j])]
    for exact_only in (True, False):
        for i, j in spans:
            span = " ".join(words[i:j])
            if exact_only:
                if span in lookup.exact:
                    return span, lookup.resolve(span)
            elif words[i] not in SCAFFOLD and words[j - 1] not in SCAFFOLD and lookup.candidates(span)[0]:
                return span, lookup.resolve(span)
    return None


def build_dish_lookup(settings, library: Library, rules: dict, spelling_map: dict | None = None) -> "DishLookup | None":
    """The lookup for the chat, or None where it must stay off: production, switched off, or the INDB files are not on this PC."""
    if settings.environment != "development" or not settings.dish_lookup:
        return None
    nutrition = Path(settings.nutrition_dir)
    indb_dir = nutrition / "indb_source"
    maps = sorted((nutrition / "releases").glob("*/ingredient_map.csv"))
    aliases = sorted((nutrition / "releases").glob("*/alias_table.csv"))
    if not (indb_dir / "recipes.xlsx").is_file() or not maps or not aliases:
        return None
    try:
        from .indb_calc import load_fct, load_ingredient_map, load_usda
        sources = Sources(usda=load_usda(settings.library_dir), ingredient_map=load_ingredient_map(maps[-1]),
                          fct={**load_fct(indb_dir / "UK_fct.xlsx", "uk_cofid"), **load_fct(indb_dir / "US_fct.xlsx", "us_fct")},
                          allowed_matches=WITH_PROXIES)
        return DishLookup(library, load_alias_rows(aliases[-1]), load_indb_recipes(indb_dir), sources, rules, spelling_map)
    except (OSError, KeyError, ValueError, IndexError, StopIteration, zipfile.BadZipFile):
        return None


# ---------------------------------------------------------------- data in
def load_alias_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_indb_recipes(indb_dir: Path) -> dict[str, IndbRecipe]:
    from .indb_compare import _table
    names = {str(r["recipe_code"]): str(r.get("recipe_name") or "") for r in _table(Path(indb_dir) / "recipes_names.xlsx") if r.get("recipe_code")}
    serving = {str(r["recipe_code"]): r for r in _table(Path(indb_dir) / "recipes_servingsize.xlsx") if r.get("recipe_code")}
    rows: dict[str, list] = {}
    for row in _table(Path(indb_dir) / "recipes.xlsx"):
        if row.get("recipe_code"):
            rows.setdefault(str(row["recipe_code"]), []).append(row)
    out = {}
    for code, name in names.items():
        s = serving.get(code) or {}
        out[code] = IndbRecipe(code=code, name=name.strip(), rows=rows.get(code, []), servings=_num(s.get("no_of_servings")),
                               serving_size=_num(s.get("size_of_servings")), serving_unit=str(s.get("servings_unit") or ""))
    return out
