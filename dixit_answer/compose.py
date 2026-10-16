"""Fetch + compose (stages 6-7): turn an answer plan into a draft with locked items, then into the final text.

Locked items ({{F1}}, {{P1}}, {{G1}}, {{Q}} ...) hold every number, food list, protocol line, caution and question. Code fills
them in after the AI has written the connecting words, so the AI can't change a number or add a food (N15, N16).
The template text below is also the safe fallback when an AI draft fails the checks twice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .facts import Fact
from .foods import FoodIndex
from .golden import Status
from .planner import AnswerPlan
from .safety import EMERGENCY_ANSWER, safety_caution
from .services import Services
from .state import ChatState

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z]+\d*)\}\}")
FOLLOWUP_CHOICE = "Which one should I show?"


@dataclass
class Draft:
    lines: list = field(default_factory=list)       # markdown lines with placeholders
    locked: dict = field(default_factory=dict)      # placeholder -> text
    facts: list = field(default_factory=list)       # Fact objects used
    foods_named: set = field(default_factory=set)   # food IDs the answer may name
    free_lines: list = field(default_factory=list)  # indexes of lines the AI may reword
    recipe: object = None                           # CheckedRecipe shown in this answer (N25, N26)
    recipe_trace: list = field(default_factory=list)
    blocked_foods: set = field(default_factory=set)         # foods this person must never be offered (N24)
    support_sources: list = field(default_factory=list)       # ids of the approved support facts used (audit)
    exclusion_texts: list = field(default_factory=list)     # lines that name a blocked food only to say it was left out
    unknown_items: list = field(default_factory=list)       # [{"word", "kind": ingredient|dish, "suggested_food_id"}] for the dietitian queue
    backlog_request: bool = False                           # no checked recipe: goes to the dietitian's recipe backlog
    suppress: set = field(default_factory=set)              # blocks the recipe flow drops (this request only: Composer is shared)

    def template(self) -> str:
        return "\n".join(self.lines)

    def fill(self, text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: self.locked.get(m.group(1), m.group(0)), text)


def _short_source(fact: Fact) -> str:
    return "USDA" if fact.source == "USDA" else "dev data"


def _dish(fact: Fact) -> str:
    """Short record name, so a number always says which dish it is for: "Dry potato (Sookhe aloo)" -> "dry potato"."""
    name = fact.food_name.split(" (")[0].split("/")[0].strip()
    return name[:1].lower() + name[1:]


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _without_blocked_staple(text: str, blocked: set) -> str:
    """Approved labels say "closer to the chapati (or rice) part of the meal"; never name chapati to someone who can't have it."""
    if "FOOD-CHAPATI" in blocked:
        text = text.replace("the chapati or rice part", "the starchy part").replace("the chapati part", "the starchy part")
    return text


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


class Composer:
    def __init__(self, services: Services, index: FoodIndex, word_lists: dict, recipes=None, allergy=None):
        self.s = services
        self.index = index
        self.words = word_lists
        self.recipes = recipes
        self.allergy = allergy

    def build(self, plan: AnswerPlan, state: ChatState, u, person=None) -> Draft:
        d = Draft()
        blocked = self.allergy.blocked_foods(person) if (person and self.allergy) else set()
        d.blocked_foods = set(blocked)
        counters: dict[str, int] = {}
        given = set(state.facts_given)

        def lock(prefix: str, text: str) -> str:
            counters[prefix] = counters.get(prefix, 0) + 1
            key = f"{prefix}{counters[prefix]}"
            d.locked[key] = text
            return "{{" + key + "}}"

        def fact_ph(fact: Fact, with_dish: bool = True) -> str:
            d.facts.append(fact)
            given.add(f"{fact.record_id}:{fact.field}")
            prefix = f"{_dish(fact)}: " if with_dish else ""
            return lock("F", f"{prefix}{fact.text} ({_short_source(fact)})")

        def free(text: str) -> None:
            d.free_lines.append(len(d.lines))
            d.lines.append(text)

        def name(fid: str) -> str:
            d.foods_named.add(fid)
            return self.index.name(fid)

        for block in plan.blocks:
            if block in d.suppress:
                continue
            if block == "emergency":
                d.lines.append(lock("T", plan.emergency_lines.get("emergency") or EMERGENCY_ANSWER))
            elif block in {"emergency_checkin", "emergency_note"}:
                d.lines.append(lock("S", plan.emergency_lines[block]))
            elif block == "greeting":
                free("You're welcome! Ask me anything about food, meals or the Dixit programme." if "thank" in u.cleaned
                     else "Hello! Ask me anything about food, meals or the Dixit programme.")
            elif block == "support_opening":
                topic = self.s.data.support_content["topics"]["wellbeing"]
                d.lines.append(lock("T", topic["opening"]))
                d.lines.append(lock("T", topic["help"]))
            elif block == "support":
                topic = self.s.data.support_content["topics"]["wellbeing"]
                d.lines.append(lock("T", topic["opening"]))
                d.lines.append(lock("T", "Everyday habits that are recommended:"))
                for fact in topic["facts"]:
                    d.lines.append("- " + lock("T", fact["text"]))
                d.lines.append(lock("T", topic["limits"]))
                d.lines.append(lock("T", topic["help"]))
                d.lines.append(lock("T", topic["urgent"]))
                d.lines.append(lock("T", topic["bridge"]))
                d.support_sources = [f["id"] for f in topic["facts"]]
            elif block == "out_of_scope":
                free("I can only help with food, meals and the Dixit programme. Is there something about your meals I can help with?")
            elif block == "opening":
                free("Thank you for asking. Many people work on their weight, and steady changes work best.")
            elif block == "ack":
                free("Thanks, noted.")
            elif block == "direct":
                for fid in [f for f in plan.pairs_with if f in d.blocked_foods]:      # "what sabji goes with chapati" for a gluten allergy
                    text = f"I left out {self.index.name(fid)} because of {self.recipes._block_reason(fid, person)}."
                    d.exclusion_texts.append(text)
                    d.lines.append(lock("T", text))
                plan.pairs_with = [f for f in plan.pairs_with if f not in d.blocked_foods]
                intro, committed = self._direct(plan, u, name)
                if intro:
                    free(intro)
                if committed:
                    d.lines.append(lock("T", committed))   # statements of fact or limits stay exactly as written
            elif block == "safety_caution":
                text = safety_caution(state)
                if text:
                    d.lines.append("**First, a quick check:** " + lock("C", text))
            elif block == "protocol_full":
                d.lines.append("**How Dixit meals work:**")
                for line in self.s.protocol_full():
                    d.lines.append("- " + lock("P", line))
            elif block == "protocol_reminder":
                d.lines.append(lock("P", self.s.protocol_reminder()))
            elif block == "protocol_amounts":
                for step in ("nuts", "salad", "sprouts"):
                    d.lines.append(f"- **{_cap(step)}:** " + lock("P", self.s.protocol_amount(step, blocked)))
            elif block == "protocol_amount":
                d.lines.append("**For the sprouts step:** " + lock("P", self.s.protocol_amount("sprouts", blocked)))
            elif block == "meal_place":
                d.lines.append("**In your meal:** " + lock("P", self.s.meal_place()))
            elif block == "between_meals":
                if plan.frequency_note:
                    d.lines.append(lock("T", "The programme's position is two meals a day. I don't have approved evidence on other "
                                              "eating patterns to share here."))
                d.lines.append("**Between meals:** " + lock("P", self.s.between_meals()))
            elif block == "choices":
                for group in self.s.choices(plan.topic, blocked):
                    foods = _join([name(f) for f in group.foods])
                    d.lines.append(lock("G", f"**{_without_blocked_staple(group.label, blocked)}:** {foods}"))   # approved wording: locked as a whole
            elif block == "focus_compare":
                for fid in plan.focus_foods:
                    if fid in blocked:
                        self._blocked_line(fid, person, d, lock, prefix="- ")
                        continue
                    group = self.s.group_of(fid)
                    label = {"choose_more_often": "a fill-up sabji, choose more often",
                             "fine_but_starchy": "starchy: fine, but closer to the chapati part",
                             "choose_less_often": "have less often"}.get(group.direction if group else "", "")
                    # Same field for every food so they line up; a comparison may repeat a number (R11 exception).
                    fact = self.s.pick_fact(fid, set(), fields=("energy_kcal",)) or self.s.pick_fact(fid, set())
                    parts = [f"- **{_cap(name(fid))}**"]
                    if label:
                        parts.append(": " + lock("G", _without_blocked_staple(label, blocked)))
                    if fact:
                        parts.append(" · " + fact_ph(fact))
                    d.lines.append("".join(parts))
            elif block == "food_position":
                for fid in plan.focus_foods:
                    name(fid)
                    if fid in blocked:
                        self._blocked_line(fid, person, d, lock)
                    else:
                        text, pos = self.s.position_for(fid, person.status if person else None)
                        d.lines.append(lock("T", text))
                        if pos is not None and pos.allowed == "depends_on_status":
                            d.lines.append(lock("T", "The nutrition facts below are for information only, not permission."))
            elif block == "food_facts":
                for fid in plan.focus_foods:
                    if fid in blocked:
                        continue
                    group = self.s.group_of(fid)
                    if group and group.direction == "fine_but_starchy":
                        part = "the starchy part of your meal" if "FOOD-CHAPATI" in blocked else "the chapati or rice part of the meal"
                        d.lines.append("**Good to know:** " + lock("G", f"{name(fid)} is a starchy vegetable, closer to {part}") + ".")
                    fact = self.s.pick_fact(fid, given)
                    if fact:
                        d.lines.append(f"**{_cap(name(fid))}** · " + fact_ph(fact))
                    elif not group:
                        d.lines.append(lock("T", f"We don't have reliable nutrition numbers for {name(fid)} yet."))
            elif block == "lookup_facts":
                self._lookup_lines(plan, u, d, given, fact_ph, name, person, lock)
            elif block == "unknown_food":
                d.lines.append(lock("T", "I couldn't find that food in our data yet. Could you tell me its name another way?"))
            elif block == "tip":
                keys = list(plan.focus_foods) + ([plan.topic] if plan.topic == "sabji" else [])
                for text, facts, _tip_id in self.s.tips(keys, given, blocked=blocked):
                    filled = text
                    for ph_key, fact in zip(("A", "B"), facts):
                        filled = filled.replace("{{" + ph_key + "}}", f"{fact.text} ({_short_source(fact)})")
                        d.facts.append(fact)
                        given.add(f"{fact.record_id}:{fact.field}")
                    d.lines.append("**Tip:** " + lock("K", filled))
            elif block == "tips_list":
                self._tips_lines(plan, state, d, lock, given)
            elif block == "recipe":
                self._recipe_block(plan, state, u, person, d, lock, free, name, given)
            elif block == "sources":
                recent = state.facts_given[-3:]
                if not recent:
                    d.lines.append(lock("T", "I haven't given any numbers in this chat yet."))
                for item in recent:
                    rid = item.split(":")[0]
                    rec = self.s.library.get(rid)
                    if rec:
                        src = "USDA FoodData Central" if not self.s.library.is_indian(rid) else "Swastham Indian meal library (development only)"
                        d.lines.append("- " + lock("T", f"{rec['food_name']}: {src}, record {rid}"))
            elif block == "status_position":
                pos = self.s.golden.fruit_position(person.status if person else Status())
                d.lines.append(lock("T", pos.text))
            elif block == "struggle":
                self._struggle_lines(u, d, lock)
            elif block == "boundary":
                d.lines.append(lock("T", "Your doctor or dietitian decides what's right for you personally, including any medicines."))
            elif block == "profile_note":
                note = self._profile_note(person, state)
                if note:
                    d.exclusion_texts.append(note)          # names an allergen only to say it was kept in mind
                    d.lines.append(lock("T", note))
            elif block == "followup" and plan.followup:
                if plan.followup["id"] == "FU-SPROUTS" and "FOOD-EGG" in d.blocked_foods:
                    plan.followup["text"] = "For the sprouts step, do you have moong or matki?"   # never offer a blocked food
                if plan.followup["id"] == "FU-PREFERENCE" and "FOOD-EGG" in d.blocked_foods:
                    plan.followup["text"] = "Do you eat vegetarian or non-vegetarian?"
                d.lines.append(lock("Q", plan.followup["text"]))
        for fid in plan.focus_foods + plan.ingredients + list(u.foods):
            d.foods_named.add(fid)
        return d

    # ---------- what was kept in mind ----------
    CONDITION_LABELS = {"obesity": "your weight goal", "hypertension": "high blood pressure", "diabetes": "diabetes",
                        "thyroid": "thyroid", "pcos": "PCOS"}

    def _profile_note(self, person, state) -> str:  # noqa: D401
        """One line saying what the answer was checked against, so the person can see the profile was used."""
        if person is None:
            return ""
        allergies = [self.allergy.label(a) for a in (person.allergies or []) if isinstance(a, str)]
        parts = [f"allergy to {_join(allergies)}"] if allergies else []
        parts += [self.CONDITION_LABELS.get(c, c) for c in person.conditions if isinstance(c, str)]
        if person.diet_type in {"vegetarian", "jain", "egg", "non_vegetarian"} and person.diet_type != "non_vegetarian":
            parts.append({"vegetarian": "vegetarian diet", "jain": "Jain diet", "egg": "eggs-ok diet"}[person.diet_type])
        parts = list(dict.fromkeys(parts))
        if not parts:
            return ""
        return "Kept in mind for you: " + _join(parts) + "."

    # ---------- allergy / avoid notes ----------
    def _blocked_line(self, fid: str, person, d: Draft, lock, prefix: str = "") -> None:
        food = self.index.foods[fid]
        reason = self.recipes._block_reason(fid, person) if (self.recipes and person) else "what you told me"
        text = f"{_cap(food['name'])} won't suit you because of {reason}. Please check with your doctor or dietitian."
        d.exclusion_texts.append(text)
        d.lines.append(prefix + lock("T", text))

    def _tips_lines(self, plan, state, d: Draft, lock, given: set) -> None:
        keys = ["recipe", *plan.ingredients, *state.home_foods]
        for text, facts, _tip_id in self.s.tips(keys, given, limit=3, blocked=d.blocked_foods):
            filled = text
            for ph_key, fact in zip(("A", "B"), facts):
                filled = filled.replace("{{" + ph_key + "}}", f"{fact.text} ({_short_source(fact)})")
                d.facts.append(fact)
                given.add(f"{fact.record_id}:{fact.field}")
            d.lines.append("- " + lock("K", filled))

    # ---------- recipes (stage 6d) ----------
    def _recipe_block(self, plan, state, u, person, d: Draft, lock, free, name, given: set) -> None:
        reply = bool(plan.reply_slot)
        if person is None:                                  # no profile at all: allergies unknown, so the most careful rules apply
            from .person import Person
            person = Person()
        out = self.recipes.run(person=person, have=list(plan.ingredients), dish_ids=list(plan.recipe_ids),
                               unknown_words=[] if reply else list(u.unknown_words),
                               suggestion=None if reply else u.suggestion, wants_list=plan.wants_list,
                               home_foods=list(state.home_foods))
        d.recipe_trace = out.trace
        if plan.unknown_dish and not plan.reply_slot:
            d.unknown_items.append({"word": plan.unknown_dish, "kind": "dish", "suggested_food_id": ""})
            d.lines.append(lock("T", f"I don't have a checked recipe for \"{plan.unknown_dish}\" yet; I've passed it to our dietitian."))
            d.backlog_request = True
        for fid, why in out.left_out:
            text = f"I left out {self.index.name(fid)} because of {why}."
            d.exclusion_texts.append(text)
            d.lines.append(lock("T", text))
        for word in out.unknown_words:
            d.unknown_items.append({"word": word, "kind": "ingredient", "suggested_food_id": ""})
        if out.status == "confirm_food":
            food = out.suggestion["food_id"]
            d.unknown_items.append({"word": out.suggestion["word"], "kind": "ingredient", "suggested_food_id": food})
            d.lines.append(lock("T", f"I don't know \"{out.suggestion['word']}\" yet."))
            if food in d.blocked_foods:                      # never suggest a food this person must not have, even as a question
                plan.followup = None
                d.suppress.add("meal_place")
                return
            plan.followup = {"id": "FU-CONFIRM", "slot": "confirm_food", "text": f"Did you mean {self.index.name(food)}?"}
            state.pending_confirm = out.suggestion
            d.suppress.add("meal_place")
            return
        if out.unknown_words:
            d.lines.append(lock("T", "I left out " + _join([f'"{w}"' for w in out.unknown_words]) +
                                " because it isn't in our food list yet; it has been passed to our dietitian."))
            d.backlog_request = True
        if out.status == "choices":
            for dish, reasons in out.rejected:
                text = f"I can't show {dish} for you: {reasons[0]}."
                d.exclusion_texts.append(text)
                d.lines.append(lock("T", text))
            if plan.ingredients and not out.rejected:
                have = _join([self.index.name(f) for f in plan.ingredients if f not in d.blocked_foods
                              and self.index.foods[f]["category"] not in {"pantry", "sweet", "fruit"}])
                intro = f"With {have} you can make any of these:" if have else "Here are some dishes you could make:"
            else:
                intro = "Here are some light dishes you could make instead:" if out.rejected else "Here are some light dishes you could make:"
            d.lines.append(lock("T", intro))
            for recipe in out.choices:
                more = f" (also needs {_join([self.index.name(f) for f in recipe.also_need])})" if recipe.also_need else ""
                d.lines.append("- " + lock("T", recipe.name + more))
            state.recipe_choices = [r.recipe_id for r in out.choices]
            if not (plan.followup and plan.followup.get("id") == "FU-ALLERGY"):        # the allergy question comes first, once
                plan.followup = {"id": "FU-RECIPE-CHOICE", "slot": "recipe_choice", "text": FOLLOWUP_CHOICE}
            d.suppress.add("meal_place")
            return
        if out.status == "recipe":
            self._render_recipe(out.recipe, plan, d, lock, free, name, state)
            return
        # tips_only (R9): honest about it, say why, give approved cooking tips, and log the request for the dietitian.
        d.backlog_request = out.backlog
        for dish, reasons in out.rejected:
            text = f"I can't show {dish} for you: {reasons[0]}."
            d.exclusion_texts.append(text)
            d.lines.append(lock("T", text))
        d.lines.append(lock("T", "We don't have a checked recipe for this yet, so here is what helps most:"))
        self._tips_lines(plan, state, d, lock, given)

    def _render_recipe(self, recipe, plan, d: Draft, lock, free, name, state=None) -> None:
        d.recipe = recipe
        if not recipe.also_need and state is not None and state.ingredients_on_hand and recipe.origin == "approved" and plan.recipe_ids:
            # A dish picked from a list for what the person has: say what else it needs (never a food they cannot have).
            have = self.index.with_generics(set(state.ingredients_on_hand))
            recipe.also_need = [f for f in self.recipes.recipes[recipe.recipe_id]["main_ingredients"] if f not in have and f not in d.blocked_foods]
        for item in recipe.ingredients:
            d.foods_named.add(item["food"])
        used = [f for f in plan.ingredients if f in {i["food"] for i in recipe.ingredients} and f not in d.blocked_foods
                and self.index.foods[f]["category"] != "pantry"] if not plan.recipe_ids else []
        if used:                                   # only foods that really are in the recipe, never words from the message
            free(f"With {_join([name(f) for f in used])} you can make this:")
        elif plan.recipe_ids:
            free("Here's the recipe:")
        d.lines.append("**" + lock("T", recipe.name) + "**")
        d.lines.append(lock("T", recipe.label))
        d.lines.append(lock("T", f"**You need** ({recipe.makes}):"))       # locked: the AI must not hint at portions
        for line in self.recipes.ingredient_lines(recipe):
            d.lines.append("- " + lock("K", line))
        d.lines.append("**How:**")
        for step in recipe.steps:
            d.lines.append("- " + lock("K", step))
        totals = self.recipes.totals_text(recipe)
        if totals:
            d.lines.append("**Whole dish:** " + lock("F", totals))
        if recipe.repairs:
            note = _join(recipe.repairs)             # e.g. "oil instead of ghee": names what was removed, not an offer
            d.exclusion_texts.append(note)
            d.lines.append("**Adjusted for you:** " + lock("T", note))
        if recipe.why_it_fits and recipe.origin == "approved":
            d.lines.append(lock("T", recipe.why_it_fits))
        if not plan.recipe_ids:                    # something they said they have that this recipe doesn't use: say so, never drop it silently
            in_recipe = {i["food"] for i in recipe.ingredients}
            unused = [f for f in plan.ingredients if f not in in_recipe and f not in d.blocked_foods
                      and self.index.foods[f]["category"] not in {"pantry", "sweet", "fruit"}]   # those have their own lines or are never cooked
            if unused:
                d.lines.append(lock("T", f"This recipe doesn't use your {_join([self.index.name(f) for f in unused])}; "
                                         "ask me for another recipe if you'd like to use it."))
        if recipe.also_need:
            d.lines.append("**You'd also need:** " + lock("T", _join([self.index.name(f) for f in recipe.also_need])))

    # ---------- helpers ----------
    def _direct(self, plan: AnswerPlan, u, name) -> tuple[str, str]:
        """(free intro the AI may reword, committed statement that stays locked)."""
        if plan.type == "food_choice_guidance":
            if plan.reply_slot == "home_foods":
                return "Here's how the sabjis you cook compare:", ""
            if plan.focus_foods:
                return "Here's how these compare:", ""
            if plan.topic == "sabji":
                staple = name(plan.pairs_with[0]) if plan.pairs_with else "Your meal"
                return f"{_cap(staple)} goes well with most sabjis.", "No vegetable is off-limits in the programme."
            return "", "The Dixit programme doesn't ban foods. It changes how you eat, and some foods are worth choosing more often."
        if plan.type == "neutral_food_comparison":
            return "Here's how these compare:", ""
        if plan.type == "meal_from_ingredients":
            if plan.ingredients:
                have = _join([name(f) for f in plan.ingredients])
                return (f"With {have} you can make a simple dry sabji the way you usually do.",
                        "We don't have a checked recipe for it yet, so here is what helps most:")
            return "", "We don't have checked recipes yet, so here is what helps most when you cook:"
        if plan.type == "recipe_request":
            return "", "We don't have checked recipes yet. These cooking tips make the most difference:"
        if plan.type == "protocol_explain":
            return "Here's how the Dixit programme works:", ""
        if plan.type == "meal_protocol_application":
            return "Here's what the programme says about meal timing:", ""
        if plan.type == "support_struggle":
            return "That's common, especially in the first weeks.", ""
        return "", ""

    def _lookup_lines(self, plan, u, d, given, fact_ph, name, person=None, lock=None) -> None:
        field = "energy_kcal"
        for word, fld in (("carb", "carbohydrate_g"), ("protein", "protein_g"), ("fat", "fat_g"), ("fiber", "fiber_g")):
            if re.search(rf"\b{word}\b", u.cleaned):
                field = fld
        if plan.focus_foods:
            for fid in plan.focus_foods:
                if fid in d.blocked_foods:
                    self._blocked_line(fid, person, d, lock)
                    continue
                fact = self.s.pick_fact(fid, set(), fields=(field,))
                if fact:
                    d.lines.append(f"{_cap(name(fid))} · " + fact_ph(fact))
                else:
                    d.lines.append(f"We don't have a reliable {field.split('_')[0]} value for {name(fid)} yet.")
            if self.index.foods[plan.focus_foods[0]].get("unit_weight_g") is None and re.search(r"\b(one|1|a|per)\b", u.cleaned):
                d.lines.append("We don't have a reliable weight for one piece yet, so I can't give a per-piece number.")
            return
        d.lines.append("I couldn't find that food in our data yet. Could you tell me its name another way?")

    def _struggle_lines(self, u, d, lock) -> None:
        if re.search(r"\b(hungry|hunger|craving|cravings)\b", u.cleaned):
            d.lines.append("**Between meals:** " + lock("P", self.s.between_meals()))
            d.lines.append("**At your next meal:** " + lock("P", "eat until you feel satisfied, starting with nuts, salad and sprouts."))
            d.lines.append(lock("T", "If you feel shaky, sweaty or dizzy, especially if you take diabetes medicine, eat something "
                                     "and contact your doctor."))
        else:
            d.lines.append(lock("T", "Weight changes slowly and differently for each person. If nothing changes over several weeks, "
                                     "talk to your doctor or Dixit coach."))
