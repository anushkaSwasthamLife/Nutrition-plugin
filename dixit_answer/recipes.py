"""Hybrid recipe flow (plugin steps 3-8, R1-R9): collection recipes first, AI-drafted + gated second, fallback last.

R1 map pantry · R2 doctor pack / allergy / avoid / diet filter · R3 limits, strictest layer first · R4 collection match, searched
by tier (doctor-approved, dietitian-signed, reference) · R5 AI draft (food IDs + grams + steps only) · R6 calculate the whole dish
· R7 recipe gate · R8 repair one ingredient, or scale a collection dish down, then AI retry (max 2) · R9 fallback.
Three outcomes: a found and fits (shown as written), b found but changed by code (swap or scale), c not found (AI draft).
Nothing unchecked is shown: a recipe reaches the person only with an explicit gate pass, and with a badge naming its source.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from .foods import FoodIndex
from .lists import method_allowed
from .loader import FLAG_MACRO_MISMATCH, FLAG_MACROS_INCOMPLETE, Library, SwasthamData
from .person import AllergyFilter, Person
from .programme import current as programme
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
MIN_SCALE = Decimal("0.5")          # a dish is never scaled below half: smaller than that is a different dish, not an adapted one
SCALE_STEP = Decimal("0.05")
TIERS = ("doctor", "programme", "dietitian", "reference")   # search order (step 5); programme = a recipe book the pack lists
# A dish asked for by name that we don't hold: a similar one is offered when it shares the dish type ("paratha") or a main food,
# and enough of its name. The words below say nothing about which dish it is.
SIMILAR_STOP = {"recipe", "recipes", "make", "how", "to", "a", "an", "the", "of", "with", "and", "ki", "ka", "ke", "dish", "healthy",
                "indian", "style", "easy", "simple", "homemade", "fresh", "mixed", "mix"}
DISH_TYPES = {"paratha", "chilla", "cheela", "khichdi", "khichadi", "curry", "sabji", "subji", "sabzi", "soup", "salad", "kabab", "kebab",
              "bhurji", "pancake", "rice", "pulao", "dal", "raita", "appe", "amboli", "keema", "masala", "fry", "thalipeeth", "upma",
              "dosa", "uttapam", "pasta", "cakes", "wrap", "bhaji", "usal", "stew", "noodle", "noodles"}
SIMILAR_MIN = 0.3
# Within a tier, a list of dishes leads with the main dishes of a home meal; grain sides (chillas, parathas) come last.
KIND_ORDER = {"sabji": 0, "dal": 1, "protein": 2, "khichdi": 3, "one_dish": 4, "salad": 5, "chilla": 6, "grain": 7}


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
    tier: str = ""                   # doctor | programme | dietitian | reference (collection recipes only)
    source_name: str = ""            # the doctor's name for a doctor-tier recipe
    source_signed: bool = False      # the doctor pack or the recipe is signed (not test data)
    scale: Decimal | None = None     # set when code scaled the whole dish (outcome b)
    book: dict | None = None         # a recipe from the programme's book: {"id", "page", "title", "signed"}
    assumed: list = field(default_factory=list)          # amounts the book left open ("to taste") and the grams assumed
    trace_items: list = field(default_factory=list)      # a pinch or a few leaves: not counted
    optional_items: list = field(default_factory=list)   # optional in the book: left out and not counted
    yield_info: dict | None = None   # the book's yield {"count", "unit", "per_serving"}: "5 vati", 1 vati a serving
    servings: Decimal | None = None  # how many book servings the whole dish makes; the gate checks one of them


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
        self.pantry = {f for f, food in index.foods.items() if food["category"] == "pantry" and f not in index.book_only}
        self.last_leftover: list[str] = []
        self.alias_to_recipe = {}
        for rid, recipe in self.recipes.items():
            for alias in [recipe["name"], *recipe.get("aliases", [])]:
                self.alias_to_recipe[clean(alias.split(" (")[0])] = rid

    @property
    def golden(self):
        """The rules of the patient's programme (from the doctor pack), for this request."""
        return programme().rules

    # ------------------------------------------------------------------ helpers
    def approved_label(self) -> str:
        if str(self.collection.get("status", "")).upper() == "TEST ONLY":
            return "Swastham test recipe (draft, not yet dietitian-approved)."
        return "Swastham recipe, dietitian-approved."

    def similar(self, request: str, person: Person, limit: int = 3) -> list[str]:
        """Recipes like a dish we don't hold ("methi paratha" -> Moringa paratha), best first, then by tier. Name-word overlap,
        and it must share the dish type or a main food: "chicken curry" finds the chicken curry, not every curry."""
        words = set(clean(request).split()) - SIMILAR_STOP
        if not words:
            return []
        foods = self.index.with_generics({m.food_id for m in self.index.find(clean(request), already_clean=True)})
        scored = []
        for rid, raw in self.recipes.items():
            if not self.usable(rid, person):
                continue
            name_words = set()
            for name in [raw["name"], *raw.get("aliases", [])]:
                name_words |= set(clean(name.split(" (")[0]).split())
            name_words -= SIMILAR_STOP
            shared = words & name_words
            if not shared:
                continue
            score = len(shared) / len(words | name_words)
            same_type = bool(shared & DISH_TYPES)
            same_food = bool(foods & self.index.with_generics(set(raw["main_ingredients"])))
            if score >= SIMILAR_MIN and (same_type or same_food or len(shared) >= 2):
                scored.append((-score, TIERS.index(self.tier_of(rid, person)), rid))
        return [rid for *_, rid in sorted(scored)[:limit]]

    def draft_for_dish(self, request: str, person: Person) -> RecipeOutcome:
        """Outcome c for a named dish nobody has written: the AI drafts it from our food IDs and grams only, and the dish is
        recalculated from USDA and gated like any other (fixed repairs, then at most 2 retries). Never with unknown allergies."""
        started = time.monotonic()
        out = RecipeOutcome(status="tips_only")
        limits = self.compile_limits(person)
        blocked = self.allergy.blocked_foods(person)
        unverifiable = [n for n, l in limits.items() if not l.verifiable]
        if not (self.ai_enabled and person.allergies_known and not person.unmapped_allergies and not unverifiable):
            why = ("off in this environment" if not self.ai_enabled else "allergies unknown" if not person.allergies_known
                   else f"limits can't be checked: {unverifiable}" if unverifiable else "an allergy we could not map")
            out.trace.append(f"R5 (named dish): AI drafting skipped ({why})")
            return out
        allowed = {f for f, food in self.index.foods.items() if f not in self.index.book_only and f not in blocked
                   and food.get("ingredient_record") and not food.get("dry_weight")
                   and food["category"] in {"vegetable", "starchy_vegetable", "pulse", "dairy", "egg", "pantry", "nut"}}
        feedback: list[str] = []
        for attempt in range(3):                  # first draft + at most 2 retries
            if time.monotonic() - started > TIME_BUDGET_SECONDS:
                out.trace.append("R5 (named dish): time budget used up")
                break
            out.ai_attempts += 1
            request_data = {"dish_request": request[:80],
                            "available": [{"food_id": f, "name": self.index.name(f)} for f in sorted(allowed)],
                            "limits_whole_dish": {n: float(l.release_at.quantize(Decimal('1'))) for n, l in limits.items()},
                            "previous_attempt_failed": feedback}
            try:
                draft = self.llm.draft_dish(request_data)
            except Exception as exc:              # provider errors -> fallback, never a crash
                out.trace.append(f"R5 (named dish): draft failed ({exc.__class__.__name__}: {str(exc)[:80]})")
                break
            if draft is None:
                out.trace.append("R5 (named dish): no model to draft with")
                break
            recipe, problem = self._validate_draft(draft, allowed)
            if recipe is None:
                feedback = [f"invalid draft: {problem}"]
                out.trace.append(f"R5 (named dish): attempt {attempt + 1} rejected ({problem})")
                continue
            self.calculate(recipe)
            recipe.gate = self.gate(recipe, person, limits, blocked)
            for _ in range(2):                    # R8: fixed repairs, then a retry
                if recipe.gate.passed or not self.repair(recipe, recipe.gate):
                    break
                self.calculate(recipe)
                recipe.gate = self.gate(recipe, person, limits, blocked)
            out.trace.append(f"R5-R8 (named dish): attempt {attempt + 1}: "
                             f"{'pass' if recipe.gate.passed else recipe.gate.reasons + recipe.gate.details}")
            if recipe.gate.passed:
                out.status, out.recipe = "recipe", recipe
                return out
            feedback = recipe.gate.reasons
        return out

    def usable(self, rid: str, person: Person) -> bool:
        """A recipe from a programme's book is only for patients whose programme lists that book; every other recipe is shared."""
        book = self.recipes[rid].get("book")
        pack = person.doctor_pack
        return not book or (pack is not None and book.get("source_book") in pack.recipe_books)

    def tier_of(self, rid: str, person: Person) -> str:
        """Step 5: doctor-approved (in this patient's doctor pack), dietitian-signed (the recipe carries a sign-off), else reference."""
        pack = person.doctor_pack
        if pack is not None and rid in pack.approved_recipes:
            return "doctor"
        raw = self.recipes[rid]
        if raw.get("book") and self.usable(rid, person):
            return "programme"
        if raw.get("approved_by") and raw.get("approved_on") and str(self.collection.get("status", "")).upper() != "TEST ONLY":
            return "dietitian"
        return "reference"

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
        """R3: strictest limit per nutrient. Layers in order: the doctor pack, then 'general' + the person's conditions, then the
        clinician's targets; the limit is the lowest value, less what was eaten today if that is recent."""
        cfg = self.limits_cfg
        margin = _d(cfg["release_margin"])
        verifiable = set(cfg["verifiable_nutrients"])
        conditions = {"general", *person.conditions}
        daily: dict[str, tuple[Decimal, list]] = {}
        pack = person.doctor_pack
        for row in (pack.limits if pack is not None else ()):
            if row["condition"] not in conditions or row.get("daily_max") is None:
                continue
            value = _d(row["daily_max"])
            cur = daily.get(row["nutrient"])
            if cur is None or value < cur[0]:
                daily[row["nutrient"]] = (value, [f"doctor pack {pack.doctor_id}"])
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
        for item in self._expanded(recipe.ingredients):
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

    def _expanded(self, ingredients: list) -> list:
        """A composite food (the book's mixed-grain flour) counts as its parts, in proportion; everything else as it is."""
        out = []
        for item in ingredients:
            parts = (self.index.foods.get(item["food"]) or {}).get("composition")
            if not parts:
                out.append(item)
                continue
            whole = sum(_d(p["grams"]) for p in parts)
            out += [{"food": p["food"], "grams": _d(item["grams"]) * _d(p["grams"]) / whole} for p in parts]
        return out

    def gate(self, recipe: CheckedRecipe, person: Person, limits: dict[str, Limit], blocked: set[str]) -> Gate:
        """R7: explicit pass only. Allergens / avoid / diet, Golden Rules, limits at 90% of the limit, missing data."""
        g = Gate(passed=False)
        foods = [i["food"] for i in recipe.ingredients]
        in_steps = [f for f in self.step_foods(recipe) if f not in foods] if recipe.book else []
        for fid in foods + in_steps:
            if fid in blocked:
                where = "contains" if fid in foods else "has a step with"
                g.reasons.append(f"it {where} {self.index.name(fid)}, which isn't suitable for you")
                g.failed_tags += [f"allergen:{a}" for a in self.index.foods[fid].get("allergens", [])] or [f"blocked:{fid}"]
        for tag, reason in self.golden.recipe_violations(foods + in_steps, self.index.foods, person.status, recipe.kind, recipe.meal_place):
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
            total = recipe.totals[nutrient] / recipe.servings if recipe.servings else recipe.totals[nutrient]
            if total > limit.release_at:
                # Limits are never shown to the person (they would read as personal advice); numbers go to the audit log.
                g.reasons.append(f"it is higher in {NUTRIENT_WORDS[nutrient]} than our limit")
                g.details.append(f"{nutrient}: {total.quantize(Decimal('1'))} > {limit.release_at.quantize(Decimal('1'))}")
                g.failed_nutrients.append(nutrient)
        if missing_foods:
            g.reasons.append("no nutrition data for " + ", ".join(self.index.name(f) for f in missing_foods))
        step_issue = "" if recipe.book else self.steps_problem(recipe)      # book steps are the book's words: checked as foods above
        if step_issue:
            g.reasons.append(step_issue)
            g.failed_tags.append("steps")
        g.passed = not g.reasons
        return g

    def step_foods(self, recipe: CheckedRecipe) -> list[str]:
        """Foods the steps name that are not in the ingredient list ("serve with chapati"): no amount, so not counted, but every
        allergy, diet and Golden Rule check sees them."""
        have = self.index.with_generics({i["food"] for i in recipe.ingredients})
        text = clean(" ".join(recipe.steps))
        return sorted({m.food_id for m in self.index.find(text, already_clean=True)} - have)

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

    def scale(self, recipe: CheckedRecipe, gate: Gate, limits: dict[str, Limit]) -> str | None:
        """R8 (outcome b): scale the whole dish down when it fails only on checkable limits. Never below MIN_SCALE, never for an
        allergen, a Golden Rule, the steps or missing data: a smaller amount of a wrong food is still wrong."""
        if recipe.origin != "approved" or gate.passed or gate.failed_tags or not gate.failed_nutrients or recipe.scale is not None:
            return None                                    # an AI draft (outcome c) is fixed by repairs and retries, never by scaling
        factor = Decimal(1)
        for nutrient in gate.failed_nutrients:
            limit = limits.get(nutrient)
            total = recipe.totals.get(nutrient)
            if total is not None and recipe.servings:
                total = total / recipe.servings
            if limit is None or not limit.verifiable or total is None or total <= 0:
                return None
            factor = min(factor, limit.release_at / total)
        factor = (factor / SCALE_STEP).to_integral_value(rounding=ROUND_FLOOR) * SCALE_STEP
        if factor < MIN_SCALE or factor >= 1:
            return None
        for item in recipe.ingredients:
            item["grams"] = float(_d(item["grams"]) * factor)
            item["unit"] = ""                              # "2 medium" is no longer true; the display works it out again
        pct = int(factor * 100)
        recipe.scale = factor
        recipe.makes = f"{recipe.makes}, made smaller: {pct}% of the usual amounts"
        desc = f"all amounts cut to {pct}%"
        recipe.repairs.append(desc)
        return desc

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
            suggestion: dict | None, wants_list: bool, home_foods: list[str], exclude: list[str] | None = None,
            exclude_foods: list[str] | None = None) -> RecipeOutcome:
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

        def fit(recipe: CheckedRecipe) -> CheckedRecipe:
            """Gate, then R8: up to two fixed repairs (also for collection recipes, e.g. ghee -> oil for a milk allergy), then a
            scale (collection recipes only)."""
            check(recipe)
            for _ in range(2):
                if recipe.gate.passed or not self.repair(recipe, recipe.gate):
                    break
                check(recipe)
            if self.scale(recipe, recipe.gate, limits):
                check(recipe)
            return recipe

        def tier_rank(rid: str) -> int:
            return TIERS.index(self.tier_of(rid, person))

        # R4: approved collection.
        skip = set(exclude or [])                   # "other than paneer bhurji": never offered back
        no_food = set(exclude_foods or [])          # "without onion": no dish that uses it
        if no_food:
            skip |= {rid for rid, raw in self.recipes.items() if no_food & {i["food"] for i in raw["ingredients"]}}
        skip |= {rid for rid in self.recipes if not self.usable(rid, person)}       # another programme's book
        candidates = [self._from_collection(rid, person) for rid in dish_ids if rid in self.recipes and rid not in skip]
        matchable = set(usable_have)
        if not candidates and usable_have:
            # "dal" on its own means any of the dals; a named dal means that one
            matchable = set(usable_have) | {f for f, food in self.index.foods.items() if food.get("generic") in set(usable_have)}
            ranked = []
            for rid, raw in self.recipes.items():
                if rid in skip:
                    continue
                main = set(raw["main_ingredients"])
                overlap = main & matchable
                if overlap:
                    ranked.append((len(main - matchable) == 0, -tier_rank(rid), len(overlap), -len(main), rid))
            ranked.sort(key=lambda t: t[4])
            ranked.sort(key=lambda t: t[:4], reverse=True)          # full matches first, then by tier; the recipe list order decides ties
            full = [self._from_collection(rid, person) for is_full, _, _, _, rid in ranked if is_full]
            partial = [self._from_collection(rid, person) for is_full, _, _, _, rid in ranked if not is_full]
        else:
            full, partial = candidates, []
        if wants_list and not candidates and not usable_have:
            pool = sorted((rid for rid in self.recipes if rid not in skip),
                          key=lambda rid: (tier_rank(rid), KIND_ORDER.get(self.recipes[rid]["kind"], 9),
                                           -len(set(self.recipes[rid]["main_ingredients"]) & set(home_foods))))
            passed = [r for r in (check(self._from_collection(rid, person)) for rid in pool) if r.gate.passed]
            out.status, out.choices = "choices", passed[:3]
            out.trace.append(f"R4: offering {[r.name for r in out.choices]}")
            if out.choices:
                return out
        passed_full: list[CheckedRecipe] = []
        for recipe in full:
            fit(recipe)
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
                    fit(recipe)
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
            pool = sorted((rid for rid in self.recipes if rid not in skip),
                          key=lambda rid: (tier_rank(rid), -len(set(self.recipes[rid]["main_ingredients"]) & set(home_foods))))
            asked_main = {m for rid in dish_ids if rid in self.recipes for m in self.recipes[rid]["main_ingredients"]}
            pool.sort(key=lambda rid: -len(asked_main & set(self.recipes[rid]["main_ingredients"])))      # dishes with the same main food first (stable)
            alternatives = [r for r in (check(self._from_collection(rid, person)) for rid in pool if rid not in dish_ids)
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
                    "meal_place": programme().text("draft_meal_place"),
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
                fit(recipe)                       # R8: fixed repairs first, then a retry
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

    def _from_collection(self, rid: str, person: Person | None = None) -> CheckedRecipe:
        person = person or Person()
        raw = self.recipes[rid]
        tier = self.tier_of(rid, person)
        pack = person.doctor_pack
        book = raw.get("book")
        recipe = CheckedRecipe(recipe_id=rid, origin="approved", name=raw["name"], makes=raw["makes"], kind=raw["kind"],
                               ingredients=[dict(i) for i in raw["ingredients"]], steps=list(raw["steps"]),
                               why_it_fits=raw.get("why_it_fits", ""), meal_place=raw.get("meal_place", "home_meal"),
                               tier=tier, source_name=pack.doctor_name if tier == "doctor" else "",
                               source_signed=(pack.signed if tier == "doctor" else bool(book and book.get("signed")) if book
                                              else tier == "dietitian"),
                               book=dict(book) if book else None, assumed=list(raw.get("assumed") or []),
                               trace_items=list(raw.get("trace_items") or []), optional_items=list(raw.get("optional_items") or []))
        made = raw.get("yield") or {}
        if made.get("count") and made.get("per_serving"):
            recipe.yield_info = dict(made)
            recipe.servings = _d(made["count"]) / _d(made["per_serving"])
        elif made.get("count"):
            recipe.yield_info = dict(made)
        recipe.label = self.label_for(recipe)
        return recipe

    def label_for(self, recipe: CheckedRecipe) -> str:
        """The source sentence shown under the dish name (step 8). Test data says so."""
        if recipe.origin != "approved":
            return recipe.label or AI_LABEL
        if recipe.book:                                    # the programme's book: credited to the book, never to a person
            page = f", page {recipe.book['page']}" if recipe.book.get("page") else ""
            test = "" if recipe.source_signed else " (transcribed from the book; not yet checked by our dietitian)"
            title = recipe.book.get("title", "the book")
            if recipe.repairs:
                return f"A version of a {title} recipe{page}, tailored for your health and taste{test}."
            return f"A recipe from {programme().identity['programme']}'s {title} book{page}{test}."
        if recipe.tier == "doctor":
            lead = f"Adapted for you from a recipe recommended by {recipe.source_name}" if recipe.repairs else f"Recommended by {recipe.source_name}"
            return lead + ("." if recipe.source_signed else " (test pack, not yet signed by the doctor).")
        return self.approved_label()

    def _block_reason(self, fid: str, person: Person) -> str:
        food = self.index.foods[fid]
        if person.doctor_pack is not None and fid in person.doctor_pack.never_foods:
            return "your doctor's programme"
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

    def per_unit_text(self, recipe: CheckedRecipe) -> str:
        """A book dish: the calories in one of the book's own measures ("about 180 kcal in each vati; the book makes 5 vati")."""
        made = recipe.yield_info or {}
        kcal = recipe.totals.get("energy_kcal")
        if not made.get("count") or kcal is None or recipe.scale is not None:
            return ""
        each = (kcal / _d(made["count"])).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        count = f"{float(made['count']):g}"
        return f"About {each} kcal in each {made['unit']}; the book's recipe makes about {count} {made['unit']}."

    def nutrition_of(self, rid: str, person: Person) -> CheckedRecipe | None:
        """One of our recipes with its totals worked out, for "calories in jeera aloo" or "how many calories in it" after a recipe.
        None when this person's programme can't use it (a book their pack doesn't list)."""
        if rid not in self.recipes or not self.usable(rid, person):
            return None
        recipe = self._from_collection(rid, person)
        self.calculate(recipe)
        return recipe

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


def recipe_badge(recipe: CheckedRecipe) -> dict:
    """Step 8: the source badge for a shown recipe. kind: doctor | dietitian | reference | adapted | ai_checked."""
    test = "" if recipe.source_signed else " (test)"
    if recipe.origin != "approved":
        return {"kind": "ai_checked", "text": "AI-drafted, checked", "recipe_id": recipe.recipe_id}
    if recipe.book:
        title = recipe.book.get("title", "Book")
        if recipe.repairs:
            return {"kind": "adapted", "text": f"Tailored from a {title} recipe{test}", "recipe_id": recipe.recipe_id,
                    "tier": recipe.tier, "changes": list(recipe.repairs), "book": recipe.book}
        return {"kind": "programme", "text": f"{title} recipe · {programme().identity['name']}{test}", "recipe_id": recipe.recipe_id,
                "tier": recipe.tier, "book": recipe.book}
    source = {"doctor": f"{recipe.source_name}'s recipe", "dietitian": "a dietitian-approved recipe"}.get(recipe.tier, "a reference recipe")
    if recipe.repairs:
        return {"kind": "adapted", "text": f"Adapted from {source}{test}", "recipe_id": recipe.recipe_id, "tier": recipe.tier,
                "changes": list(recipe.repairs)}
    text = {"doctor": f"Recommended by {recipe.source_name}", "dietitian": "Dietitian-approved recipe"}.get(recipe.tier, "Reference recipe")
    return {"kind": recipe.tier or "reference", "text": text + test, "recipe_id": recipe.recipe_id, "tier": recipe.tier}


def recipe_main(recipe: CheckedRecipe, collection: dict) -> list[str]:
    raw = collection.get(recipe.recipe_id)
    return list(raw["main_ingredients"]) if raw else []


def _only_minutes(step: str) -> bool:
    """Digits in AI steps are allowed only as cooking times ('12 to 15 minutes')."""
    stripped = re.sub(r"\b\d+\s*(?:to|-|–)?\s*\d*\s*(?:minutes?|mins?|hours?|seconds?|whistles?)\b", "", step)
    return not any(ch.isdigit() for ch in stripped)
