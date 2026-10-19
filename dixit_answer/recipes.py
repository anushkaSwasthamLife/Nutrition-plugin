"""Hybrid recipe flow (stage 6d, R1-R9): approved recipes first, AI-drafted + gated second, fallback last.

R1 map pantry · R2 allergy / avoid / diet filter · R3 per-meal limits · R4 approved match · R5 AI draft (food IDs + grams +
steps only) · R6 calculate the whole dish · R7 recipe gate · R8 repair one ingredient, then AI retry (max 2) · R9 fallback.
Nothing unchecked is shown: a recipe reaches the person only with an explicit gate pass.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .foods import FoodIndex
from .lists import method_allowed
from .loader import FLAG_MACRO_MISMATCH, FLAG_MACROS_INCOMPLETE, Library, SwasthamData
from .person import AllergyFilter, Person
from .textnorm import clean

MACROS = ("energy_kcal", "carbohydrate_g", "fiber_g", "protein_g", "fat_g")
# Library v1.0.4 adds these six for USDA records (null = USDA publishes no value). They are summed for the gate only and never shown in answers.
EXTRA_NUTRIENTS = ("sodium_mg", "sugars_g", "saturated_fat_g", "cholesterol_mg", "potassium_mg", "phosphorus_mg")
NUTRIENTS = MACROS + EXTRA_NUTRIENTS
NUTRIENT_WORDS = {"energy_kcal": "kcal", "carbohydrate_g": "carbohydrate", "fiber_g": "fibre", "protein_g": "protein",
                  "fat_g": "fat", "sodium_mg": "sodium", "added_sugar_g": "added sugar", "sugars_g": "sugar",
                  "saturated_fat_g": "saturated fat", "cholesterol_mg": "cholesterol", "potassium_mg": "potassium",
                  "phosphorus_mg": "phosphorus"}
ALLOWED_KINDS = {"sabji", "dal"}
AI_LABEL = ("AI-suggested recipe, checked by our system against general limits for your conditions. "
            "Your doctor or dietitian has the final say.")
MAX_CHOICES = 6
TIME_BUDGET_SECONDS = 15


@dataclass
class Limit:
    nutrient: str
    limit_max: Decimal
    release_at: Decimal
    conditions: list
    verifiable: bool


@dataclass
class Gate:
    passed: bool
    reasons: list = field(default_factory=list)
    failed_nutrients: list = field(default_factory=list)
    failed_tags: list = field(default_factory=list)       # e.g. "allergen:milk", "golden_rule:sugar"
    details: list = field(default_factory=list)           # numbers behind a failure; audit log only
    unverifiable: list = field(default_factory=list)


@dataclass
class CheckedRecipe:
    recipe_id: str
    origin: str                      # approved | ai_suggested
    name: str
    makes: str
    kind: str
    ingredients: list                # [{"food", "grams", "unit"}]
    steps: list
    why_it_fits: str = ""
    totals: dict = field(default_factory=dict)       # nutrient -> Decimal (only fully known nutrients)
    missing: dict = field(default_factory=dict)      # nutrient -> [food IDs without data]
    gate: Gate | None = None
    repairs: list = field(default_factory=list)
    also_need: list = field(default_factory=list)
    meal_place: str = "home_meal"
    label: str = ""


@dataclass
class RecipeOutcome:
    status: str                      # recipe | choices | confirm_food | tips_only
    recipe: CheckedRecipe | None = None
    choices: list = field(default_factory=list)       # CheckedRecipe list (offered names)
    left_out: list = field(default_factory=list)      # (food ID, reason) removed by the allergy / avoid / diet filter
    unknown_words: list = field(default_factory=list)
    suggestion: dict | None = None                    # {"word", "food_id"}
    trace: list = field(default_factory=list)
    ai_attempts: int = 0
    backlog: bool = False                             # request goes to the dietitian's recipe backlog
    rejected: list = field(default_factory=list)      # (dish name, reasons) for a named dish that failed the gate


def _d(value) -> Decimal:
    return Decimal(str(value))


def _valid_amount(value) -> Decimal | None:
    """A nutrient amount from the library as an exact decimal, or None when it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return _d(value)


class RecipeService:
    def __init__(self, library: Library, data: SwasthamData, index: FoodIndex, allergy: AllergyFilter, llm,
                 ai_enabled: bool, golden=None):
        self.golden = golden
        self.library = library
        self.data = data
        self.index = index
        self.allergy = allergy
        self.llm = llm
        self.ai_enabled = ai_enabled
        self.collection = data.recipes
        self.recipes = {r["recipe_id"]: r for r in data.recipes["recipes"]}
        self.limits_cfg = data.condition_limits
        self.factors = {k: _d(v) for k, v in data.cooking_factors["factors"].items()}
        self.pantry = {f for f, food in index.foods.items() if food["category"] == "pantry"}
        self.last_leftover: list[str] = []
        self.alias_to_recipe = {}
        for rid, recipe in self.recipes.items():
            for alias in [recipe["name"], *recipe.get("aliases", [])]:
                self.alias_to_recipe[clean(alias.split(" (")[0])] = rid

    # ------------------------------------------------------------------ helpers
    def approved_label(self) -> str:
        if str(self.collection.get("status", "")).upper() == "TEST ONLY":
            return "Swastham test recipe (draft, not yet dietitian-approved)."
        return "Swastham recipe, dietitian-approved."

    def find_recipes_in_text(self, cleaned: str) -> list[str]:
        """Recipe names in a message. The longest alias wins and its words are used up, so "palak paneer" never also
        matches the shorter "palak"."""
        found = []
        text = f" {cleaned} "
        for alias in sorted(self.alias_to_recipe, key=len, reverse=True):
            needle = f" {alias} "
            if alias and needle in text:
                rid = self.alias_to_recipe[alias]
                if rid not in found:
                    found.append(rid)
                text = text.replace(needle, " ")
        self.last_leftover = text.split()           # words of the message that are not part of a dish name
        return found

    def compile_limits(self, person: Person) -> dict[str, Limit]:
        """R3: strictest limit per nutrient across 'general' + the person's conditions; the limit is the table's value, less what was eaten today if that is recent."""
        cfg = self.limits_cfg
        margin = _d(cfg["release_margin"])
        verifiable = set(cfg["verifiable_nutrients"])
        conditions = {"general", *person.conditions}
        daily: dict[str, tuple[Decimal, list]] = {}
        for row in cfg["limits"]:
            if row["condition"] not in conditions or row.get("daily_max") is None:
                continue
            value = _d(row["daily_max"])
            cur = daily.get(row["nutrient"])
            if cur is None or value < cur[0]:
                daily[row["nutrient"]] = (value, [row["condition"]])
        for nutrient, target in person.clinician_targets.items():
            value = _d(target)
            if nutrient not in daily or value < daily[nutrient][0]:
                daily[nutrient] = (value, ["clinician target"])
        intake = person.recent_intake()
        limits = {}
        for nutrient, (value, sources) in daily.items():
            limit = value
            if nutrient in intake:
                limit = max(value - _d(intake[nutrient]), Decimal(0))
            limits[nutrient] = Limit(nutrient, limit, limit * margin, sources, nutrient in verifiable)
        return limits

    def calculate(self, recipe: CheckedRecipe) -> None:
        """R6: whole-dish totals from grams x per-100 g USDA values, exact decimals; a missing value is an error, never 0."""
        totals = {n: Decimal(0) for n in NUTRIENTS}
        missing: dict[str, list] = {}
        for item in recipe.ingredients:
            food = self.index.foods.get(item["food"])
            rid = (food or {}).get("ingredient_record")
            rec = self.library.get(rid) if rid else None
            flags = self.library.flags.get(rid, set()) if rid else set()
            ok = rec is not None and not self.library.is_indian(rid) and not (flags & {FLAG_MACRO_MISMATCH, FLAG_MACROS_INCOMPLETE})
            per100 = (rec or {}).get("nutrients_per_100g") if isinstance((rec or {}).get("nutrients_per_100g"), dict) else {}
            for n in NUTRIENTS:
                value = _valid_amount(per100.get(n)) if ok else None
                if value is None:                           # absent, null, text, NaN, infinite, negative or a boolean: all are "no data"
                    missing.setdefault(n, []).append(item["food"])
                else:
                    totals[n] += value * _d(item["grams"]) / Decimal(100) * self.factors.get(n, Decimal(1))
        recipe.totals = {n: v for n, v in totals.items() if n not in missing}
        recipe.missing = missing

    def gate(self, recipe: CheckedRecipe, person: Person, limits: dict[str, Limit], blocked: set[str]) -> Gate:
        """R7: explicit pass only. Allergens / avoid / diet, Golden Rules, limits at 90% of the limit, missing data."""
        g = Gate(passed=False)
        foods = [i["food"] for i in recipe.ingredients]
        for fid in foods:
            if fid in blocked:
                g.reasons.append(f"it contains {self.index.name(fid)}, which isn't suitable for you")
                g.failed_tags += [f"allergen:{a}" for a in self.index.foods[fid].get("allergens", [])] or [f"blocked:{fid}"]
        for tag, reason in self.golden.recipe_violations(foods, self.index.foods, person.status, recipe.kind, recipe.meal_place):
            g.reasons.append(reason)
            g.failed_tags.append(tag)
        missing_foods: list[str] = []
        for nutrient, limit in limits.items():
            if not limit.verifiable:
                g.unverifiable.append(nutrient)
                if recipe.origin == "ai_suggested":
                    g.reasons.append(f"{NUTRIENT_WORDS.get(nutrient, nutrient)} can't be checked (not in our data yet)")
                    g.failed_nutrients.append(nutrient)
                continue
            if nutrient in recipe.missing:
                missing_foods += [f for f in recipe.missing[nutrient] if f not in missing_foods]
                g.failed_nutrients.append(nutrient)
                continue
            total = recipe.totals[nutrient]
            if total > limit.release_at:
                # Limits are never shown to the person (they would read as personal advice); numbers go to the audit log.
                g.reasons.append(f"it is higher in {NUTRIENT_WORDS[nutrient]} than our limit")
                g.details.append(f"{nutrient}: {total.quantize(Decimal('1'))} > {limit.release_at.quantize(Decimal('1'))}")
                g.failed_nutrients.append(nutrient)
        if missing_foods:
            g.reasons.append("no nutrition data for " + ", ".join(self.index.name(f) for f in missing_foods))
        step_issue = self.steps_problem(recipe)
        if step_issue:
            g.reasons.append(step_issue)
            g.failed_tags.append("steps")
        g.passed = not g.reasons
        return g

    def steps_problem(self, recipe: CheckedRecipe) -> str:
        """N26: steps (and the name) may only mention foods that are in the ingredient list."""
        have = self.index.with_generics({i["food"] for i in recipe.ingredients})
        text = clean(" ".join([recipe.name, *recipe.steps]))
        extra = sorted({m.food_id for m in self.index.find(text, already_clean=True)} - have)
        return f"steps mention {', '.join(self.index.name(f) for f in extra)}, which isn't in the ingredients" if extra else ""

    def repair(self, recipe: CheckedRecipe, gate: Gate) -> str | None:
        """R8: one fixed repair from the swap table for a failed nutrient or tag. Returns a description, or None."""
        problems = set(gate.failed_nutrients) | set(gate.failed_tags)
        for rule in self.data.swap_table["repairs"]:
            if not problems & set(rule["fixes"]):
                continue
            idx = next((i for i, item in enumerate(recipe.ingredients) if item["food"] == rule["food"]), None)
            if idx is None:
                continue
            item = recipe.ingredients[idx]
            name = self.index.name(rule["food"])
            if rule["action"] == "reduce":
                new = _d(rule["to_g"]) if "to_g" in rule else _d(item["grams"]) * _d(rule["factor"])
                if new >= _d(item["grams"]):
                    continue
                item["grams"] = float(new)
                item["unit"] = ""
                desc = f"less {name}"
            elif rule["action"] == "swap":
                item["food"] = rule["to"]
                item["unit"] = ""
                desc = f"{self.index.name(rule['to'])} instead of {name}"
            else:
                recipe.ingredients.pop(idx)
                desc = f"no {name}"
            for old, new_word in (rule.get("step_words") or {}).items():
                recipe.steps = [" ".join(s.replace(old, new_word).split()) for s in recipe.steps]
            recipe.repairs.append(desc)
            return desc
        return None

    # ------------------------------------------------------------------ R5: AI draft
    def _validate_draft(self, draft: dict, allowed: set[str]) -> tuple[CheckedRecipe | None, str]:
        try:
            return self._validate_draft_unsafe(draft, allowed)
        except Exception:                                  # noqa: BLE001 - whatever shape the AI sent, it is simply rejected
            return None, "malformed draft"

    def _validate_draft_unsafe(self, draft: dict, allowed: set[str]) -> tuple[CheckedRecipe | None, str]:
        if not isinstance(draft, dict):
            return None, "draft is not an object"
        name, items, steps = draft.get("name"), draft.get("ingredients"), draft.get("steps")
        if not isinstance(name, str) or not 3 <= len(name.strip()) <= 60:
            return None, "bad name"
        if not _plain_text(name) or any(ch.isdigit() for ch in name):
            return None, "name has markup, links or numbers"
        method = draft.get("method")
        if method is not None and (not isinstance(method, str)
                                   or not method_allowed(self.data.white_lists, method, self.library.environment)):
            return None, "cooking method is not on the white list"
        if not isinstance(items, list) or not 2 <= len(items) <= 15:
            return None, "bad ingredient list"
        if not isinstance(steps, list) or not 2 <= len(steps) <= 6 or not all(isinstance(s, str) and 5 <= len(s) <= 220 for s in steps):
            return None, "bad steps"
        if not all(_plain_text(s) for s in steps):
            return None, "steps have markup or links"
        clean_items, seen = [], set()
        for item in items:
            food_id = item.get("food_id") if isinstance(item, dict) else None
            if not isinstance(food_id, str) or food_id not in allowed:
                return None, f"ingredient not allowed: {str(food_id)[:40]}"
            if food_id in seen:
                return None, f"ingredient listed twice: {food_id}"
            seen.add(food_id)
            grams = item.get("grams")
            if isinstance(grams, bool) or not isinstance(grams, (int, float)) or not math.isfinite(float(grams)):
                return None, "grams not a number"
            grams = float(grams)
            if not 0.5 <= grams <= 1000:
                return None, "grams out of range"
            clean_items.append({"food": food_id, "grams": grams, "unit": ""})
        if not all(_only_minutes(s) for s in steps):
            return None, "steps carry amounts (amounts belong in the ingredient list)"
        recipe = CheckedRecipe(recipe_id=f"AI-{int(time.time() * 1000) % 10_000_000}", origin="ai_suggested",
                               name=name.strip(), makes="1 dish", kind="sabji", ingredients=clean_items,
                               steps=[s.strip() for s in steps], label=AI_LABEL)
        return recipe, ""

    # ------------------------------------------------------------------ the flow
    def run(self, *, person: Person, have: list[str], dish_ids: list[str], unknown_words: list[str],
            suggestion: dict | None, wants_list: bool, home_foods: list[str]) -> RecipeOutcome:
        started = time.monotonic()
        out = RecipeOutcome(status="tips_only")

        # R1: unknown pantry words never reach the AI. A close match is confirmed first.
        out.unknown_words = list(unknown_words)
        if suggestion:
            out.status, out.suggestion = "confirm_food", suggestion
            out.trace.append(f"R1: '{suggestion['word']}' unknown, asking about {self.index.name(suggestion['food_id'])}")
            return out
        if unknown_words:
            out.trace.append(f"R1: unknown {unknown_words}, left out and queued for review")

        # R2: allergy / avoid / diet filter. Unknown allergies -> approved recipes free of the major allergens only.
        blocked = self.allergy.blocked_foods(person)
        if not person.allergies_known:
            blocked = blocked | self.allergy.major_allergen_foods()
        if person.unmapped_allergies:
            out.trace.append(f"R2: allergy not recognised {person.unmapped_allergies}: staying clear of all common allergens")
        fruit = [f for f in have if self.golden.is_fruit(self.index.foods[f]["category"], f)] if person.status.value != "non_diabetic" else []
        for fid in have:
            if fid in blocked:
                out.left_out.append((fid, self._block_reason(fid, person)))
            elif fid in fruit:
                out.left_out.append((fid, "the programme's fruit rule, which depends on your diabetes status"))
        usable_have = [f for f in have if f not in blocked and f not in fruit]
        out.trace.append(f"R2: blocked {len(blocked)} foods; allergies {'known' if person.allergies_known else 'unknown'}")

        # R3: limits.
        limits = self.compile_limits(person)
        out.trace.append("R3: " + ", ".join(f"{n}<= {l.release_at.quantize(Decimal('1'))}" + ("" if l.verifiable else " (can't verify)")
                                             for n, l in limits.items()))

        def check(recipe: CheckedRecipe) -> CheckedRecipe:
            self.calculate(recipe)
            recipe.gate = self.gate(recipe, person, limits, blocked)
            return recipe

        # R4: approved collection.
        candidates = [self._from_collection(rid) for rid in dish_ids if rid in self.recipes]
        matchable = set(usable_have)
        if not candidates and usable_have:
            # "dal" on its own means any of the dals; a named dal means that one
            matchable = set(usable_have) | {f for f, food in self.index.foods.items() if food.get("generic") in set(usable_have)}
            ranked = []
            for rid, raw in self.recipes.items():
                main = set(raw["main_ingredients"])
                overlap = main & matchable
                if overlap:
                    ranked.append((len(main - matchable) == 0, len(overlap), -len(main), rid))
            ranked.sort(key=lambda t: t[3])
            ranked.sort(key=lambda t: t[:3], reverse=True)          # stable: the recipe list order decides ties
            full = [self._from_collection(rid) for is_full, _, _, rid in ranked if is_full]
            partial = [self._from_collection(rid) for is_full, _, _, rid in ranked if not is_full]
        else:
            full, partial = candidates, []
        if wants_list and not candidates and not usable_have:
            pool = sorted(self.recipes, key=lambda rid: -len(set(self.recipes[rid]["main_ingredients"]) & set(home_foods)))
            passed = [r for r in (check(self._from_collection(rid)) for rid in pool) if r.gate.passed]
            out.status, out.choices = "choices", passed[:3]
            out.trace.append(f"R4: offering {[r.name for r in out.choices]}")
            if out.choices:
                return out
        passed_full: list[CheckedRecipe] = []
        for recipe in full:
            check(recipe)
            for _ in range(2):                    # R8 also applies to approved recipes (e.g. ghee -> oil for a milk allergy)
                if recipe.gate.passed or not self.repair(recipe, recipe.gate):
                    break
                check(recipe)
            out.trace.append(f"R4/R7: {recipe.name}: {'pass' if recipe.gate.passed else recipe.gate.reasons}"
                             + (f" after repairs {recipe.repairs}" if recipe.repairs else ""))
            if recipe.gate.passed:
                passed_full.append(recipe)
            elif dish_ids:
                out.rejected.append((recipe.name, recipe.gate.reasons))
        if passed_full:
            options = passed_full[:MAX_CHOICES]
            if not dish_ids and len(options) < MAX_CHOICES:
                # Dishes that need one more thing from the market are offered too, marked as such.
                for recipe in partial:
                    if len(options) >= MAX_CHOICES:
                        break
                    check(recipe)
                    for _ in range(2):
                        if recipe.gate.passed or not self.repair(recipe, recipe.gate):
                            break
                        check(recipe)
                    if recipe.gate.passed:
                        recipe.also_need = [f for f in recipe_main(recipe, self.recipes) if f not in matchable]
                        options.append(recipe)
            if len(options) >= 2 and not dish_ids:
                # Several approved dishes use what the person has: offer them (up to six) instead of picking one for them.
                out.status, out.choices = "choices", options
                out.trace.append(f"R4: {len(options)} approved dishes fit what you have, offering {[r.name for r in out.choices]}")
            else:
                out.status, out.recipe = "recipe", passed_full[0]
            return out
        if dish_ids and out.rejected:
            # A named dish that doesn't suit this person: offer approved dishes that do.
            pool = sorted(self.recipes, key=lambda rid: -len(set(self.recipes[rid]["main_ingredients"]) & set(home_foods)))
            asked_main = {m for rid in dish_ids if rid in self.recipes for m in self.recipes[rid]["main_ingredients"]}
            pool.sort(key=lambda rid: -len(asked_main & set(self.recipes[rid]["main_ingredients"])))      # dishes with the same main food first (stable)
            alternatives = [r for r in (check(self._from_collection(rid)) for rid in pool if rid not in dish_ids)
                            if r.gate.passed]
            if alternatives:
                out.status, out.choices = "choices", alternatives[:3]
                out.trace.append(f"R9: named dish rejected, offering {[r.name for r in out.choices]}")
                return out

        # R5-R8: AI draft, only when allowed and safe to check.
        unverifiable = [n for n, l in limits.items() if not l.verifiable]
        # Sweets and pantry items are never "ingredients to cook with" (the programme asks to reduce sugar).
        main_have = [f for f in usable_have if f not in self.pantry and self.index.foods[f]["category"] != "sweet"
                     and not self.index.foods[f].get("dry_weight")]      # dry pulses need soaking and boiling: approved recipes only
        ai_ok = (self.ai_enabled and person.allergies_known and not person.unmapped_allergies and not unverifiable
                 and main_have and not dish_ids)
        if not ai_ok:
            why = ("off in this environment" if not self.ai_enabled else "allergies unknown" if not person.allergies_known
                   else f"limits can't be checked: {unverifiable}" if unverifiable else "nothing to cook with" if not main_have
                   else "a named dish was asked for")
            out.trace.append(f"R5: AI drafting skipped ({why})")
        else:
            allowed = set(main_have) | (self.pantry - blocked)
            feedback: list[str] = []
            for attempt in range(3):          # first draft + at most 2 retries
                if time.monotonic() - started > TIME_BUDGET_SECONDS:
                    out.trace.append("R5: time budget used up")
                    break
                out.ai_attempts += 1
                request = {
                    "must_use_any": [{"food_id": f, "name": self.index.name(f)} for f in main_have],
                    "pantry": [{"food_id": f, "name": self.index.name(f)} for f in sorted(allowed - set(main_have))],
                    "meal_place": "the home-cooked part of a Dixit meal (after nuts, salad and sprouts); a sabji or dal",
                    "limits_whole_dish": {n: float(l.release_at.quantize(Decimal('1'))) for n, l in limits.items()},
                    "previous_attempt_failed": feedback,
                }
                try:
                    draft = self.llm.draft_recipe(request)
                except Exception as exc:          # provider errors -> fallback, never a crash
                    out.trace.append(f"R5: draft failed ({exc.__class__.__name__}: {str(exc)[:80]})")
                    break
                recipe, problem = self._validate_draft(draft, allowed)
                if recipe is None:
                    feedback = [f"invalid draft: {problem}"]
                    out.trace.append(f"R5: attempt {attempt + 1} rejected ({problem})")
                    continue
                check(recipe)
                for _ in range(2):                # R8: fixed repairs first
                    if recipe.gate.passed or not self.repair(recipe, recipe.gate):
                        break
                    check(recipe)
                out.trace.append(f"R5-R8: attempt {attempt + 1}: {'pass' if recipe.gate.passed else recipe.gate.reasons + recipe.gate.details}"
                                 + (f" after repairs {recipe.repairs}" if recipe.repairs else ""))
                if recipe.gate.passed:
                    out.status, out.recipe = "recipe", recipe
                    return out
                feedback = recipe.gate.reasons

        # R9: fallback. A partial approved match that passes, else tips with "couldn't check".
        for recipe in partial:
            check(recipe)
            if recipe.gate.passed:
                recipe.also_need = [f for f in recipe_main(recipe, self.recipes) if f not in usable_have]
                out.status, out.recipe = "recipe", recipe
                out.trace.append(f"R9: partial approved match {recipe.name}, also need {recipe.also_need}")
                return out
        out.status, out.backlog = "tips_only", True
        out.trace.append("R9: no checked recipe; tips + recipe backlog")
        return out

    def _from_collection(self, rid: str) -> CheckedRecipe:
        raw = self.recipes[rid]
        return CheckedRecipe(recipe_id=rid, origin="approved", name=raw["name"], makes=raw["makes"], kind=raw["kind"],
                             ingredients=[dict(i) for i in raw["ingredients"]], steps=list(raw["steps"]),
                             why_it_fits=raw.get("why_it_fits", ""), meal_place=raw.get("meal_place", "home_meal"),
                             label=self.approved_label())

    def _block_reason(self, fid: str, person: Person) -> str:
        food = self.index.foods[fid]
        for allergen in person.allergies or []:
            if fid in self.allergy.foods_for([allergen]):
                return f"your {self.allergy.label(allergen)} allergy"
        if fid in person.avoid_foods:
            return "your list of foods to avoid"
        if not person.allergies_known and set(food.get("allergens", [])) & set(self.allergy.map["major"]):
            return "a common allergen (tell me if you have no allergies)"
        return f"your {person.diet_type.replace('_', '-')} diet"

    # ------------------------------------------------------------------ display
    def ingredient_lines(self, recipe: CheckedRecipe) -> list[str]:
        lines = []
        for item in recipe.ingredients:
            food = self.index.foods[item["food"]]
            grams = Decimal(str(item["grams"])).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            unit = item.get("unit") or ""
            if not unit:
                for unit_name, weight in (food.get("unit_weights_g") or {}).items():
                    if unit_name in ("medium", "piece", "clove") and weight:
                        count = round(float(item["grams"]) / weight)
                        if count >= 1:
                            plural = unit_name + "s" if (count > 1 and unit_name in ("piece", "clove")) else unit_name
                            unit = f"about {count} {plural}"
                        break
            lines.append(f"{food['name']}: {grams} g" + (f" ({unit})" if unit else ""))
        return lines

    def totals_text(self, recipe: CheckedRecipe) -> str:
        parts = []
        for n in ("energy_kcal", "carbohydrate_g", "protein_g", "fat_g", "fiber_g"):
            if n in recipe.totals:
                v = recipe.totals[n].quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                parts.append(f"{v} kcal" if n == "energy_kcal" else f"{v} g {NUTRIENT_WORDS[n]}")
        text = "about " + ", ".join(parts) + " for the whole dish (calculated from USDA)" if parts else ""
        if any(n in recipe.missing for n in MACROS):      # the extra nutrients are never shown, so their gaps are not worth mentioning
            text += ("; " if text else "") + "some values aren't available"
        return text


_MARKUP_RE = re.compile(r"[<>{}\[\]|\\`*_#~]|https?:|www\.|://|@", re.IGNORECASE)


def _plain_text(text: str) -> bool:
    """No markup, braces, links or control characters: AI text that is shown to a person must be plain words."""
    return not _MARKUP_RE.search(text) and not any(ord(ch) < 32 for ch in text)


def recipe_main(recipe: CheckedRecipe, collection: dict) -> list[str]:
    raw = collection.get(recipe.recipe_id)
    return list(raw["main_ingredients"]) if raw else []


def _only_minutes(step: str) -> bool:
    """Digits in AI steps are allowed only as cooking times ('12 to 15 minutes')."""
    stripped = re.sub(r"\b\d+\s*(?:to|-|–)?\s*\d*\s*(?:minutes?|mins?|hours?|seconds?|whistles?)\b", "", step)
    return not any(ch.isdigit() for ch in stripped)
