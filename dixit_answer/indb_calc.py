"""Recipe nutrition worked out from ingredient rows (INDB plan, phase 2).

For each ingredient: grams x value per 100 g / 100. A recipe is the sum of its ingredients. Nothing here asks a language model for a
number. An ingredient with no usable value is listed, never guessed, and the recipe is marked incomplete.

The unit rules copy INDB's own conversion (INDB.do, lines 166-210, applied in the same order, a later rule overriding an earlier
one), so this calculator can be checked against INDB's published values. Plugin recipes use the food master's own gram weights.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

NUTRIENTS = ("energy_kcal", "protein_g", "fat_g", "carb_g", "fibre_g")
STRICT = frozenset({"same food"})
WITH_PROXIES = frozenset({"same food", "close proxy", "weak proxy"})
_TRACE_WORDS = {"", "tr", "n", "na", "none"}      # INDB reads these as zero


# ---------------------------------------------------------------- units to grams (INDB.do order)
def _rule(unit: str, factor: float, cats: str | None = None):
    return (unit, factor, set(cats) if cats else None)


UNIT_RULES = [
    _rule("tsp", 2, "GHBU"), _rule("tbsp", 7, "DGHBU"), _rule("tsp", 2, "D"), _rule("tbsp", 40, "F"),
    _rule("ml", 1, "GHIVX"), _rule("tsp", 2.5, "A"), _rule("tsp", 5, "M"), _rule("tbsp", 7, "A"), _rule("tsp", 2, "V"),
    _rule("tsp", 4, "T"), _rule("tbsp", 13.5, "T"), _rule("tsp", 6, "XW"), _rule("tbsp", 20, "XW"), _rule("tsp", 4, "I"),
    _rule("tbsp", 12, "I"), _rule("C", 200, "I"), _rule("tbsp", 15, "Z"), _rule("tsp", 5, "L"), _rule("tbsp", 15, "L"),
    _rule("C", 240, "L"), _rule("ml", 1, "L"), _rule("sprig", 2, "GUCA"), _rule("tsp", 0.5, "C"), _rule("tbsp", 5, "C"),
    _rule("tsp", 4, "T"), _rule("ml", 1, "T"), _rule("C", 240), _rule("ml", 1, "K"), _rule("ml", 1, "E"),
    _rule("tsp", 5, "KE"), _rule("tsp", 5, "E"), _rule("tbsp", 15, "KE"), _rule("tbsp", 15, "E"), _rule("tbsp", 8, "V"),
    _rule("C", 240, "L"), _rule("small", 2, "G"),
]


def to_grams(amount, unit: str, code: str, food_name: str = "") -> float | None:
    """Grams for one ingredient row, or None when no rule covers the unit (INDB leaves those out of its totals too)."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
        return None
    unit = (unit or "").strip()
    category = (code or "")[:1]
    grams = None
    if unit == "g":
        grams = float(amount)
    for rule_unit, factor, cats in UNIT_RULES:
        if unit == rule_unit and (cats is None or category in cats):
            grams = float(amount) * factor
    if (food_name or "") == "FENUGREEK LEAVES":
        grams = float(amount) * 2
    if unit == "pinch":
        grams = 0.125
    return grams


# ---------------------------------------------------------------- reading .xlsx without extra packages
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _column(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_xlsx(path) -> list[list]:
    """Rows of the first sheet of an .xlsx file as plain Python values, like openpyxl's values_only (no formulas evaluated).

    Written so the chat can read INDB's tables from the project's own environment, which has no spreadsheet package.
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).iter(_MAIN + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_MAIN + "t")))
        sheet = "xl/worksheets/sheet1.xml"
        if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
            first = next(iter(ET.fromstring(archive.read("xl/workbook.xml")).iter(_MAIN + "sheet")), None)
            if first is not None:
                rid = first.get(_REL + "id")
                for rel in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels")):
                    if rel.get("Id") == rid:
                        target = rel.get("Target", "").lstrip("/")
                        sheet = target if target.startswith("xl/") else "xl/" + target
        root = ET.fromstring(archive.read(sheet))
    rows: dict[int, dict[int, object]] = {}
    for row in root.iter(_MAIN + "row"):
        for cell in row.iter(_MAIN + "c"):
            ref, kind = cell.get("r", ""), cell.get("t")
            value_node = cell.find(_MAIN + "v")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(_MAIN + "t"))
            elif value_node is None or value_node.text is None:
                continue
            elif kind == "s":
                index = int(value_node.text)
                value = shared[index] if 0 <= index < len(shared) else None
            elif kind in ("str", "d"):
                value = value_node.text
            elif kind == "b":
                value = value_node.text == "1"
            elif kind == "e":
                value = None
            else:
                text = value_node.text.strip()
                try:
                    value = int(text) if re.fullmatch(r"-?\d+", text) else float(text)
                except ValueError:
                    value = None
            number_match = re.search(r"\d+", ref)
            if number_match:
                rows.setdefault(int(number_match.group()), {})[_column(ref)] = value
    if not rows:
        return []
    width = max((max(cells) for cells in rows.values() if cells), default=-1) + 1
    return [[rows.get(r, {}).get(c) for c in range(width)] for r in range(1, max(rows) + 1)]


# ---------------------------------------------------------------- values per 100 g
def number(value, blank_is_zero: bool = True) -> float | None:
    """A table cell as a number. Trace marks (and blanks, unless told otherwise) are zero as INDB reads them; anything else
    unreadable is None."""
    if value is None:
        return 0.0 if blank_is_zero else None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value >= 0 else None
    text = str(value).strip().lower()
    if text in _TRACE_WORDS:
        return 0.0 if (blank_is_zero or text not in {"", "none", "na"}) else None
    try:
        out = float(text)
    except ValueError:
        return None
    return out if math.isfinite(out) and out >= 0 else None


@dataclass
class Resolution:
    per100: dict | None          # NUTRIENTS -> value per 100 g
    kind: str                    # usda | uk_cofid | us_fct | additive | none
    reason: str = ""             # why there is no value


@dataclass
class Sources:
    """Where each INDB ingredient code gets its values. Built once, then used for every recipe."""
    usda: dict[str, dict] = field(default_factory=dict)             # library record id -> nutrients_per_100g
    ingredient_map: dict[str, dict] = field(default_factory=dict)   # INDB code -> {match, usda_id}
    fct: dict[str, dict] = field(default_factory=dict)              # INDB code -> per 100 g (UK CoFID and USDA tables)
    allowed_matches: frozenset = STRICT

    def resolve(self, code: str, code_org: str, name: str = "") -> Resolution:
        code = (code or "").strip()
        if not code or code.lower() == "none":
            return Resolution(None, "additive", "no food code (colour, essence, citric acid and the like)")
        if code in self.ingredient_map:
            entry = self.ingredient_map[code]
            if entry["match"] not in self.allowed_matches:
                return Resolution(None, "none", f"USDA match is '{entry['match']}', not allowed in this run")
            record = self.usda.get(entry["usda_id"])
            if record is None:
                return Resolution(None, "none", f"USDA record {entry['usda_id']} is not in the library")
            return Resolution(record, "usda")
        if code in self.fct:
            return Resolution(self.fct[code], self.fct[code].get("_kind", "fct"))
        return Resolution(None, "none", "no USDA mapping for this INDB code")


def _per100_from_library(record: dict) -> dict | None:
    n = record.get("nutrients_per_100g") or {}
    # A value the library does not publish (None) makes the record unusable; it is never read as zero.
    energy = number(n.get("energy_kcal"), False)
    protein, fat = number(n.get("protein_g"), False), number(n.get("fat_g"), False)
    fibre = number(n.get("fiber_g"))
    carb = number(n.get("net_carbohydrate_g"), False)
    if carb is None:
        total = number(n.get("carbohydrate_g"), False)
        carb = None if total is None or fibre is None else max(0.0, total - fibre)
    values = {"energy_kcal": energy, "protein_g": protein, "fat_g": fat, "carb_g": carb, "fibre_g": fibre}
    if energy is None or any(v is None for v in values.values()):
        return None
    return values


def load_usda(library_dir: Path) -> dict[str, dict]:
    out = {}
    path = Path(library_dir) / "01_data" / "nutrition_usda_reference.jsonl"
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                values = _per100_from_library(record)
                if values is not None and (record.get("retrieval") or {}).get("eligible"):
                    out[record["nutrition_record_id"]] = values
    return out


def load_fct(path: Path, kind: str) -> dict[str, dict]:
    """INDB's UK CoFID or US ingredient table: INDB code -> per 100 g. A row with an unreadable value is left out."""
    rows = read_xlsx(path)
    head = [str(h) for h in rows[0]]
    col = {name: head.index(name) for name in ("food_code", "energy_kcal", "carb_g", "protein_g", "fat_g", "fibre_g")}
    out = {}
    for row in rows[1:]:
        code = row[col["food_code"]]
        if not code or not str(code).strip():
            continue
        values = {n: number(row[col[n]]) for n in NUTRIENTS}
        if all(v is not None for v in values.values()):
            out[str(code).strip()] = {**values, "_kind": kind}
    return out


def load_ingredient_map(csv_path: Path) -> dict[str, dict]:
    out = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("Ingredient code") or "").strip()
            if code:
                out[code] = {"match": (row.get("Match type") or "").strip(), "usda_id": (row.get("USDA Food ID") or "").strip(),
                             "name": row.get("Ingredient (INDB name)", "")}
    return out


# ---------------------------------------------------------------- one recipe
@dataclass
class RecipeResult:
    totals: dict = field(default_factory=lambda: {n: 0.0 for n in NUTRIENTS})
    weight_g: float = 0.0              # every coded ingredient with a weight, the way INDB counts a recipe's weight
    valued_g: float = 0.0
    nonwater_g: float = 0.0
    valued_nonwater_g: float = 0.0
    unvalued: list = field(default_factory=list)     # (ingredient, grams, reason)
    unweighed: list = field(default_factory=list)    # (ingredient, unit): no rule turns this unit into grams
    parts: list = field(default_factory=list)        # (code, ingredient, grams, kcal) for every valued ingredient

    @property
    def complete(self) -> bool:
        return not self.unvalued and not self.unweighed

    @property
    def coverage(self) -> float | None:
        """Share of the recipe's weight (water left out) that has a value. None when the recipe has no weight."""
        return None if self.nonwater_g <= 0 else self.valued_nonwater_g / self.nonwater_g

    def per_100g(self) -> dict | None:
        if self.weight_g <= 0:
            return None
        return {n: self.totals[n] / (self.weight_g / 100.0) for n in NUTRIENTS}


def calculate(rows, sources: Sources) -> RecipeResult:
    """rows: dicts with food_code, food_code_org, food_name, amount, unit."""
    result = RecipeResult()
    for row in rows:
        code = str(row.get("food_code") if row.get("food_code") is not None else "")
        name = str(row.get("food_name") or row.get("ingredient_name_org") or "")
        resolution = sources.resolve(code, str(row.get("food_code_org") or ""), name)
        if resolution.kind == "additive":
            continue                                   # INDB drops rows with no food code before it adds up weight
        grams = to_grams(row.get("amount"), str(row.get("unit") or ""), code, str(row.get("food_name") or ""))
        if grams is None:
            result.unweighed.append((name, str(row.get("unit") or "")))
            continue
        water = name.strip().lower().startswith("water")
        result.weight_g += grams
        if not water:
            result.nonwater_g += grams
        if resolution.per100 is None:
            result.unvalued.append((name, grams, resolution.reason))
            continue
        result.valued_g += grams
        if not water:
            result.valued_nonwater_g += grams
        for n in NUTRIENTS:
            result.totals[n] += grams * resolution.per100[n] / 100.0
        result.parts.append((code, name, grams, grams * resolution.per100["energy_kcal"] / 100.0))
    return result
