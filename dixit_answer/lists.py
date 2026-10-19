"""White lists (only these may be used) and black lists (never use these): reading, enforcing and cross-checking.

Data lives in white_lists.json and black_lists.json. This module is the one place that reads them, so the engine, the allergy
filter, the search, the start-up check and the readiness report all apply the same rules.
"""
from __future__ import annotations

import re
from datetime import date

from .sign_off import _real_date, _unfilled
from .textnorm import clean

ENVIRONMENTS = {"development", "production"}
_FILLER = {"about", "around", "approx", "a", "an", "of", "the", "to", "taste", "some", "few"}


def _entries(cfg: dict, section: str) -> list[dict]:
    block = cfg.get(section) if isinstance(cfg, dict) else None
    rows = block.get("entries") if isinstance(block, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _allowed_in(entry: dict, environment: str) -> bool:
    allowed = entry.get("allowed_in")
    return isinstance(allowed, list) and environment in allowed


# ------------------------------------------------------------------ black lists, applied at run time
def blocked_record_ids(black: dict, environment: str) -> dict[str, str]:
    """record id -> reason, for the records blocked in this environment."""
    out = {}
    for entry in _entries(black, "blocked_records"):
        blocked_in = entry.get("blocked_in")
        if isinstance(blocked_in, list) and environment in blocked_in and isinstance(entry.get("record_id"), str):
            out[entry["record_id"]] = str(entry.get("reason", ""))
    return out


def apply_blocked_records(library, black: dict, environment: str) -> list[str]:
    """Take the blocked records out of the loaded library. Returns the ids that were removed."""
    removed = []
    for rid in blocked_record_ids(black, environment):
        if library.records.pop(rid, None) is not None:
            library.flags.pop(rid, None)
            removed.append(rid)
    return removed


def condition_blocked_foods(black: dict, conditions) -> set[str]:
    """Foods never offered to a person with any of these conditions."""
    wanted = {str(c).strip().lower() for c in (conditions or [])}
    return {e["food_id"] for e in _entries(black, "unsafe_ingredients")
            if isinstance(e.get("food_id"), str) and str(e.get("condition", "")).strip().lower() in wanted}


def derivative_foods(black: dict, allergens) -> set[str]:
    wanted = set(allergens or [])
    return {fid for e in _entries(black, "allergen_derivatives") if e.get("allergen") in wanted
            for fid in (e.get("foods") or []) if isinstance(fid, str)}


def derivative_words(black: dict) -> dict[str, list[str]]:
    """allergen -> words that stand for it"""
    out: dict[str, list[str]] = {}
    for e in _entries(black, "allergen_derivatives"):
        if isinstance(e.get("allergen"), str):
            out.setdefault(e["allergen"], []).extend(w for w in (e.get("words") or []) if isinstance(w, str))
    return out


def bad_mapping_pairs(black: dict) -> list[tuple[str, str]]:
    """(query word, record-name word) pairs the search must never return"""
    pairs = []
    for e in _entries(black, "bad_mappings"):
        q, n = clean(str(e.get("query_word", ""))), clean(str(e.get("blocked_name_word", "")))
        if q and n:
            pairs.append((q, n))
    return pairs


# ------------------------------------------------------------------ white lists, applied at run time
def method_allowed(white: dict, method: str, environment: str) -> bool:
    wanted = clean(str(method))
    for entry in _entries(white, "cooking_methods"):
        names = {clean(entry.get("id", "")), *(clean(a) for a in entry.get("aliases") or [])}
        if wanted in names and _allowed_in(entry, environment):
            return True
    return False


def unit_words(white: dict, environment: str) -> set[str]:
    words = set()
    for entry in _entries(white, "units"):
        if _allowed_in(entry, environment):
            words |= {clean(entry.get("id", "")), *(clean(a) for a in entry.get("aliases") or [])}
    words.discard("")
    return words


def unit_text_problem(text: str, allowed: set[str]) -> str | None:
    """A recipe unit like '2 cloves' or 'about 2 bunches': the words left after the numbers must be a listed unit."""
    plain = clean(text)
    if not plain:
        return None
    words = [w for w in re.sub(r"[\d/.½¼¾]+", " ", plain).split() if w not in _FILLER]
    if not words:
        return None                                  # a bare count like "1"
    phrase = " ".join(words)
    if phrase in allowed:
        return None
    return f"unit '{text}' is not on the white list"


# ------------------------------------------------------------------ start-up cross-checks
def list_problems(data, library, index, environment: str, today: date | None = None) -> list[str]:
    white, black = data.white_lists, data.black_lists
    problems: list[str] = []
    today = today or date.today()

    ids: set[str] = set()
    for section in ("recipe_sources", "cooking_methods", "units"):
        for e in _entries(white, section):
            key = str(e.get("id", ""))
            if not key or (section, key) in ids:
                problems.append(f"white list {section}: entry '{key}' is empty or repeated")
            ids.add((section, key))
            allowed = e.get("allowed_in")
            if not isinstance(allowed, list) or not allowed or set(allowed) - ENVIRONMENTS:
                problems.append(f"white list {section}/{key}: allowed_in must name development and/or production")

    # recipes: source, method and units must be on the white lists
    sources = {e.get("id"): e for e in _entries(white, "recipe_sources")}
    allowed_units = unit_words(white, environment)
    for recipe in data.recipes["recipes"]:
        rid = recipe["recipe_id"]
        source = recipe.get("source")
        if source is None:
            if environment == "production":
                problems.append(f"{rid}: no source (production needs one from the white list)")
        elif source not in sources:
            problems.append(f"{rid}: source '{source}' is not on the white list")
        elif not _allowed_in(sources[source], environment):
            problems.append(f"{rid}: source '{source}' is not allowed in {environment}")
        method = recipe.get("method")
        if method is not None and not method_allowed(white, method, environment):
            problems.append(f"{rid}: cooking method '{method}' is not on the white list for {environment}")
        for item in recipe["ingredients"]:
            problem = unit_text_problem(str(item.get("unit") or ""), allowed_units)
            if problem:
                problems.append(f"{rid}: {problem} for {environment}")

    # food master: unit weights use listed units; production aliases are approved
    for fid, food in index.foods.items():
        for unit in food.get("unit_weights_g") or {}:
            if clean(unit) not in allowed_units:
                problems.append(f"{fid}: unit weight for '{unit}' is not on the white list for {environment}")
    if environment == "production":
        approved = {(e.get("food_id"), clean(str(e.get("alias", "")))) for e in _entries(white, "ingredient_aliases")
                    if not _unfilled(e.get("approved_by")) and not _real_date(e.get("approved_on"), today)}
        missing = [(fid, a) for fid, food in index.foods.items()
                   for a in {clean(food["name"]), *(clean(x) for x in food.get("aliases", []))} if (fid, a) not in approved]
        if missing:
            problems.append(f"white list ingredient_aliases: {len(missing)} food names have no approved entry (first: {missing[0][1]})")

    # black lists point at things that exist
    for e in _entries(black, "blocked_records"):
        if not isinstance(e.get("record_id"), str) or not isinstance(e.get("blocked_in"), list) or not e["blocked_in"]:
            problems.append(f"black list blocked_records/{e.get('id')}: needs a record_id and blocked_in as a list")
        elif set(e["blocked_in"]) - ENVIRONMENTS:
            problems.append(f"black list blocked_records/{e.get('id')}: blocked_in has an unknown environment")
    blocked = blocked_record_ids(black, environment)
    for fid, food in index.foods.items():
        if food.get("ingredient_record") in blocked:
            problems.append(f"{fid}: ingredient record {food['ingredient_record']} is blocked in {environment} ({blocked[food['ingredient_record']]})")
        for rid in (food.get("fact_records") or {}).get(environment, []):
            if rid in blocked:
                problems.append(f"{fid}: fact record {rid} is blocked in {environment}")
    for e in _entries(black, "unsafe_ingredients"):
        label = f"black list unsafe_ingredients/{e.get('condition')}/{e.get('food_id')}"
        if e.get("food_id") not in index.foods:
            problems.append(f"{label}: unknown food")
        if _unfilled(e.get("condition")):
            problems.append(f"{label}: no condition")
        if environment == "production":
            if _unfilled(e.get("source"), 3):
                problems.append(f"{label}: has no real source")
            if _unfilled(e.get("reviewer")):
                problems.append(f"{label}: has no reviewer")
            reason = _real_date(e.get("review_date"), today)
            if reason:
                problems.append(f"{label}: review_date {reason}")
    allergens = data.allergen_map["allergens"]
    for e in _entries(black, "allergen_derivatives"):
        label = f"black list allergen_derivatives/{e.get('id')}"
        if e.get("allergen") not in allergens:
            problems.append(f"{label}: allergen '{e.get('allergen')}' is not in the allergen map")
        if not e.get("words") and not e.get("foods"):
            problems.append(f"{label}: no words and no foods")
        for fid in e.get("foods") or []:
            if fid not in index.foods:
                problems.append(f"{label}: unknown food {fid}")
    for e in _entries(black, "bad_mappings"):
        if not clean(str(e.get("query_word", ""))) or not clean(str(e.get("blocked_name_word", ""))):
            problems.append(f"black list bad_mappings/{e.get('id')}: needs query_word and blocked_name_word")
    # a food-master alias must not be a query word that a bad mapping blocks for the food's own record
    for q, n in bad_mapping_pairs(black):
        for fid, food in index.foods.items():
            rid = food.get("ingredient_record")
            name = clean((library.records.get(rid) or {}).get("food_name", "")) if rid else ""
            names = {clean(food["name"]), *(clean(a) for a in food.get("aliases", []))}
            if q in names and n in name.split():
                problems.append(f"{fid}: its record {rid} is blocked by bad mapping {q} -> {n}")
    return problems


# ------------------------------------------------------------------ readiness
def unsigned_counts(data_dir_files: dict[str, dict]) -> list[str]:
    """Human-readable open points: entries still without an approver or reviewer."""
    points = []
    white, black = data_dir_files.get("white_lists", {}), data_dir_files.get("black_lists", {})
    for section in ("recipe_sources", "cooking_methods", "units"):
        rows = _entries(white, section)
        open_ = [r for r in rows if _unfilled(r.get("approved_by"))]
        if open_:
            points.append(f"white list {section}: {len(open_)} of {len(rows)} entries have no approver")
    for section in ("blocked_records", "unsafe_ingredients", "allergen_derivatives", "bad_mappings"):
        rows = _entries(black, section)
        open_ = [r for r in rows if _unfilled(r.get("reviewer"))]
        if open_:
            points.append(f"black list {section}: {len(open_)} of {len(rows)} entries have no reviewer")
    unsafe = black.get("unsafe_ingredients") if isinstance(black.get("unsafe_ingredients"), dict) else {}
    if not _entries(black, "unsafe_ingredients") and (_unfilled(unsafe.get("reviewed_empty_by")) or _unfilled(unsafe.get("reviewed_empty_on"))):
        points.append("black list unsafe_ingredients: empty and not signed as empty; a doctor or dietitian lists the foods, "
                      "or signs reviewed_empty_by and reviewed_empty_on")
    if not _entries(white, "ingredient_aliases"):
        points.append("white list ingredient_aliases: empty, no food name is approved for production yet")
    return points
