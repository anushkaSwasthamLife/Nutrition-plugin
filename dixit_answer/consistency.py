"""Cross-checks between the data files, run when the engine starts (and in the tests).

A recipe that names a food the food master doesn't know, an approved-list entry pointing at a missing record, an allergen map
that misses a derivative: each of these would otherwise show up as a wrong answer for one person at one time. Here they stop
the start-up instead, with every problem listed.
"""
from __future__ import annotations


def find_problems(data, library, index, environment: str) -> list[str]:
    problems: list[str] = []
    foods = index.foods

    # ---- food master
    seen_alias: dict[str, str] = {}
    for fid, food in foods.items():
        for alias in [food["name"], *food.get("aliases", [])]:
            key = alias.lower().strip()
            if key in seen_alias and seen_alias[key] != fid:
                problems.append(f"alias '{alias}' is on both {seen_alias[key]} and {fid}")
            seen_alias[key] = fid
        for rid in (food.get("fact_records") or {}).get(environment, []):
            if rid not in library.records:
                problems.append(f"{fid}: fact record {rid} is not loaded in {environment}")
        rid = food.get("ingredient_record")
        if rid:
            if rid not in library.records:
                problems.append(f"{fid}: ingredient record {rid} is not loaded in {environment}")
            elif library.is_indian(rid):
                problems.append(f"{fid}: ingredient record {rid} is an Indian record (recipe maths uses USDA only)")
        if food.get("generic") and food["generic"] not in foods:
            problems.append(f"{fid}: generic food {food['generic']} does not exist")
        if not food.get("category"):
            problems.append(f"{fid}: no category")

    def need(food_id, where):
        if food_id not in foods:
            problems.append(f"{where}: unknown food {food_id}")

    # ---- recipes
    ids = set()
    for recipe in data.recipes["recipes"]:
        rid = recipe["recipe_id"]
        if rid in ids:
            problems.append(f"recipe id {rid} is used twice")
        ids.add(rid)
        for item in recipe["ingredients"]:
            need(item["food"], rid)
            if not isinstance(item.get("grams"), (int, float)) or item["grams"] <= 0:
                problems.append(f"{rid}: bad grams for {item.get('food')}")
        for fid in recipe["main_ingredients"]:
            need(fid, f"{rid} main ingredient")
            if fid not in {i["food"] for i in recipe["ingredients"]}:
                problems.append(f"{rid}: main ingredient {fid} is not in the ingredient list")
        if recipe.get("makes") is None or "per person" in str(recipe.get("makes")).lower():
            problems.append(f"{rid}: 'makes' must state a whole-dish amount, never per person")
        if len(recipe["steps"]) < 2:
            problems.append(f"{rid}: fewer than two steps")

    # ---- approved list, allergen map, swap table, tips
    for entry in data.approved_list["entries"]:
        if not entry.get("golden_rule_checked"):
            problems.append(f"approved list {entry['guidance_id']}: not marked golden_rule_checked")
        for fid in entry["foods"]:
            need(fid, f"approved list {entry['guidance_id']}")
    for allergen, entry in data.allergen_map["allergens"].items():
        for fid in entry["foods"]:
            need(fid, f"allergen map '{allergen}'")
    for major in data.allergen_map["major"]:
        if major not in data.allergen_map["allergens"]:
            problems.append(f"allergen map: major allergen '{major}' has no entry")
    for rule in data.swap_table["repairs"]:
        need(rule["food"], "swap table")
        if rule.get("to"):
            need(rule["to"], "swap table")
        if rule["action"] not in {"swap", "reduce", "remove"}:
            problems.append(f"swap table: unknown action {rule['action']}")
    for tip in data.cooking_tips["tips"]:
        for rid, _field in tip[environment]["compare"]:
            if rid not in library.records:
                problems.append(f"tip {tip['tip_id']}: record {rid} is not loaded in {environment}")
        for key in tip["applies_to"]:
            if key.startswith("FOOD-"):
                need(key, f"tip {tip['tip_id']}")

    # ---- every nutrient marked checkable must be one the recipe maths adds up, and the library must carry it
    from .recipes import NUTRIENTS
    for nutrient in data.condition_limits.get("verifiable_nutrients", []):
        if nutrient not in NUTRIENTS:
            problems.append(f"limits: {nutrient} is marked checkable but the recipe maths does not add it up")
        elif nutrient not in {"energy_kcal", "carbohydrate_g", "fiber_g", "protein_g", "fat_g"}:
            carried = [rid for rid in {f.get("ingredient_record") for f in foods.values()} if rid and rid in library.records
                       and (library.records[rid].get("nutrients_per_100g") or {}).get(nutrient) is not None]
            if not carried:
                problems.append(f"limits: {nutrient} is marked checkable but no ingredient record in the library has a value for it")
    for row in data.condition_limits["limits"]:
        if row["nutrient"] not in NUTRIENTS and row["nutrient"] not in {"added_sugar_g"}:
            problems.append(f"limits: {row['condition']}/{row['nutrient']} is not a nutrient we know")

    # ---- limits and the Golden Rules
    for row in data.condition_limits["limits"]:
        if row.get("daily_max") is not None and (not isinstance(row["daily_max"], (int, float)) or row["daily_max"] <= 0):
            problems.append(f"limits: {row['condition']}/{row['nutrient']} has a bad daily_max")
    if not 0 < data.condition_limits["release_margin"] <= 1:
        problems.append("limits: release_margin must be between 0 and 1")
    for key in ("full", "reminder", "meal_place", "amounts", "fruit", "positions", "forbidden_claims"):
        if key not in data.golden_rules:
            problems.append(f"golden rules: missing '{key}'")
    for step, cfg in data.golden_rules.get("amount_options", {}).items():
        if step not in data.golden_rules["amounts"]:
            problems.append(f"golden rules: amount options for '{step}' have no amount")
        for option in cfg["options"]:
            for fid in option["foods"]:
                need(fid, f"golden rules amount option '{option['text']}'")
    return problems
