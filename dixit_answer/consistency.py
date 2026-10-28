"""Cross-checks between the data files, run when the engine starts (and in the tests).

A recipe that names a food the food master doesn't know, an approved-list entry pointing at a missing record, an allergen map
that misses a derivative: each of these would otherwise show up as a wrong answer for one person at one time. Here they stop
the start-up instead, with every problem listed.
"""
from __future__ import annotations


def find_problems(data, library, index, environment: str) -> list[str]:
    problems: list[str] = []
    foods = index.foods
    if data.food_cautions:
        from .cautions import FoodCautions
        problems += FoodCautions(data.food_cautions, data.word_lists, foods).problems()

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
        for part in food.get("composition") or []:
            if part.get("food") not in foods or (foods[part["food"]].get("composition")):
                problems.append(f"{fid}: composition part {part.get('food')} is unknown or itself a mix")
            elif not foods[part["food"]].get("ingredient_record"):
                problems.append(f"{fid}: composition part {part['food']} has no USDA record")

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
    if environment == "production":
        from .sign_off import limit_problems
        problems += limit_problems(data.condition_limits)
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

    # ---- doctor packs: a pack may only name foods, recipes and nutrients we have, and production needs the doctor's signature
    packs = data.doctor_packs
    recipe_ids = {r["recipe_id"] for r in data.recipes["recipes"]}
    if packs.get("default_doctor_id") not in packs.get("packs", {}):
        problems.append(f"doctor packs: default doctor {packs.get('default_doctor_id')} has no pack")
    for did, pack in packs.get("packs", {}).items():
        if pack.get("default_programme") not in pack.get("programmes", {}):
            problems.append(f"doctor pack {did}: default programme {pack.get('default_programme')} does not exist")
        if environment == "production" and not (pack.get("approved_by") and pack.get("approved_on")):
            problems.append(f"doctor pack {did}: not signed by the doctor (approved_by, approved_on)")
        for pid, prog in pack.get("programmes", {}).items():
            from .programme import NEUTRAL_IDENTITY, NEUTRAL_TEXTS, RULE_TYPES
            rules = prog.get("rules") or {}
            if rules.get("data") and rules["data"] not in (data.programme_rules or {}):
                problems.append(f"doctor pack {did}/{pid}: rules data '{rules['data']}' is not in the manifest's programme_rules")
            for book in prog.get("recipe_books") or []:
                if book not in (data.recipe_book_maps or {}):
                    problems.append(f"doctor pack {did}/{pid}: recipe book '{book}' is not in the manifest's recipe_books")
            for rule_type in rules.get("rule_types") or []:
                if rule_type not in RULE_TYPES:
                    problems.append(f"doctor pack {did}/{pid}: unknown rule type '{rule_type}'")
            for key in prog.get("identity") or {}:
                if key not in NEUTRAL_IDENTITY:
                    problems.append(f"doctor pack {did}/{pid}: unknown identity field '{key}'")
            for key in prog.get("texts") or {}:
                if key not in NEUTRAL_TEXTS:
                    problems.append(f"doctor pack {did}/{pid}: unknown text '{key}'")
            for fid in prog.get("never_foods", []):
                need(fid, f"doctor pack {did}/{pid} never_foods")
            for rid in prog.get("approved_recipes", []):
                if rid not in recipe_ids:
                    problems.append(f"doctor pack {did}/{pid}: approved recipe {rid} is not in the recipe collection")
            for row in prog.get("limits", []):
                if row.get("nutrient") not in NUTRIENTS:
                    problems.append(f"doctor pack {did}/{pid}: limit on {row.get('nutrient')} is not a nutrient the recipe maths adds up")
                if isinstance(row.get("daily_max"), bool) or not isinstance(row.get("daily_max"), (int, float)) or row["daily_max"] <= 0:
                    problems.append(f"doctor pack {did}/{pid}: limit on {row.get('nutrient')} has a bad daily_max")
                if not isinstance(row.get("condition"), str):
                    problems.append(f"doctor pack {did}/{pid}: limit on {row.get('nutrient')} has no condition")

    # ---- router: every main topic needs a reviewed reply (the reply the main assistant falls back to)
    router = data.router_rules
    for topic in [*router.get("main_topics", {}), "greeting", "thanks", "about", "general"]:
        if not router.get("main_replies", {}).get(topic):
            problems.append(f"router rules: no main reply for '{topic}'")
    for topic in router.get("model_topics") or []:
        if topic in ("cure", "child"):
            problems.append(f"router rules: model_topics lists '{topic}', whose reply only reviewed text may give")
        elif not router.get("main_replies", {}).get(topic):
            problems.append(f"router rules: model_topics lists '{topic}', which has no reviewed reply")
    if not router.get("clarify_question"):
        problems.append("router rules: no clarify question")
    if not (router.get("model_reply_deny") or {}).get("words") or "medicine" not in (router.get("model_reply_deny") or {}).get("topics", []):
        problems.append("router rules: model replies on medicine are not checked for medicine changes (model_reply_deny)")

    # ---- the binding map describes the programme binding the manifest names (binding.py checks every ID once the package loads)
    binding_id = (data.manifest.get("programme_binding") or {}).get("id")
    if data.binding_map is not None and data.binding_map.get("binding_id") != binding_id:
        problems.append(f"binding map is for {data.binding_map.get('binding_id')!r}, the manifest's programme binding is {binding_id!r}")
    from .ladder import problems as ladder_problems
    if getattr(data, "authority_ladder", None) is not None:
        problems += ladder_problems(data.authority_ladder, data.rules_registry)

    from .lists import list_problems
    problems += list_problems(data, library, index, environment)
    return problems
