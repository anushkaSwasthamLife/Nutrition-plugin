"""Fetch + compose (stages 6-7): turn an answer plan into a draft with locked items, then into the final text.

Locked items ({{F1}}, {{P1}}, {{G1}}, {{Q}} ...) hold every number, food list, protocol line, caution and question. Code fills
them in after the AI has written the connecting words, so the AI can't change a number or add a food (N15, N16).
The template text below is also the safe fallback when an AI draft fails the checks twice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dish_lookup import AMBIGUOUS, FOUND, fact_text as dish_fact_text, find_all_in_text, plain_sentence
from .facts import Fact, fact_sentence
from .foods import FoodIndex
from .golden import Status
from .programme import current as programme
from .person import Person
from .planner import AnswerPlan
from .safety import EMERGENCY_ANSWER, safety_caution
from .services import Services
from .state import ChatState
from .facts import make_fact
from .foods import LibrarySearch
from .lists import bad_mapping_pairs
from .targets import EGG, MEAT_FISH, QUAL_NOUN, TargetSearch, wants_drinks
from .understand import SYMPTOM as SYMPTOM_RE
from .unknown_foods import COLOR_WORDS, food_phrase, grams_in, measure_in, scaled_fact, subject_of, usda_hits
from .textnorm import clean

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
    general: dict | None = None                             # general recipe fallback: what was asked, tried and shown (audit)
    gaps: list = field(default_factory=list)                # [{"topic", "reason"}]: questions the approved sources do not cover (audit: knowledge_gap)

    def template(self) -> str:
        return "\n".join(self.lines)

    def fill(self, text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: self.locked.get(m.group(1), m.group(0)), text)


def _grams(value) -> str:
    return f"{float(value):g} g"


def _src(fact: Fact) -> str:
    """The source after a number: "(USDA)", or "(calculated from ingredients)" for a dish worked out from its ingredients. The
    development-only Indian data gets no label here; the Sources panel beside the answer names it and marks it."""
    if fact.source == "USDA":
        return " (USDA)"
    return " (calculated from ingredients)" if fact.source.startswith("calculated") else ""


def _dish(fact: Fact) -> str:
    """Short record name, so a number always says which dish it is for: "Dry potato (Sookhe aloo)" -> "dry potato"."""
    name = fact.food_name.split(" (")[0].split("/")[0].strip()
    return name[:1].lower() + name[1:]


def _join(names: list[str]) -> str:
    if not names:
        return ""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _without_blocked_staple(text: str, blocked: set) -> str:
    """Approved labels say "closer to the chapati (or rice) part of the meal"; never name chapati to someone who can't have it."""
    if "FOOD-CHAPATI" in blocked:
        text = text.replace("the chapati or rice part", "the starchy part").replace("the chapati part", "the starchy part")
    return text


def _short_dish(name: str, names: list[str]) -> str:
    """"Cabbage rolls (dry) ((Pattagobhi rolls) (dry))" -> "Cabbage rolls", or "Cabbage rolls (dry)" when two names would collide."""
    base = name.split(" (")[0]
    if sum(n.split(" (")[0] == base for n in names) < 2:
        return base
    first = re.match(r"[^()]*\([^()]*\)", name)
    return first.group(0) if first else name


LABEL_FOR = {"energy_kcal": "calories", "protein_g": "g of protein", "carb_g": "g of carbohydrate", "fat_g": "g of fat", "fibre_g": "g of fibre"}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


class Composer:
    def __init__(self, services: Services, index: FoodIndex, word_lists: dict, recipes=None, allergy=None, dishes=None, general=None,
                 cautions=None):
        self.dishes = dishes
        self.general = general
        self.s = services
        self.index = index
        self.words = word_lists
        self.recipes = recipes
        self.allergy = allergy
        self.cautions = cautions                     # FoodCautions: condition, medicine and pregnancy lines (cautions.py)

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
            return lock("F", f"{prefix}{fact.text}{_src(fact)}")

        def fact_line(fact: Fact, fid: str | None = None) -> str:
            """One locked sentence for a fact: "A bowl of dal has about 139 calories. (USDA)"."""
            d.facts.append(fact)
            given.add(f"{fact.record_id}:{fact.field}")
            return lock("F", f"{fact_sentence(fact, self.index.name(fid) if fid else '')}{_src(fact)}")

        def free(text: str) -> None:
            d.free_lines.append(len(d.lines))
            d.lines.append(text)

        def name(fid: str) -> str:
            d.foods_named.add(fid)
            return self.index.name(fid)

        P = programme()                                   # the patient's programme: its names, own lines and rules
        for block in plan.blocks:
            if block in d.suppress:
                continue
            if block == "emergency":
                d.lines.append(lock("T", plan.emergency_lines.get("emergency") or EMERGENCY_ANSWER))
            elif block == "harmful":
                d.lines.append(lock("T", plan.emergency_lines["harmful"]))
            elif block in {"emergency_checkin", "emergency_note"}:
                d.lines.append(lock("S", plan.emergency_lines[block]))
            elif block == "greeting":
                if "thank" in u.cleaned or u.cleaned in {"thx", "ty", "ok thanks", "okay thanks"}:
                    free(P.fill("You're welcome! Ask me anything about food, meals or {programme}."))
                elif u.cleaned in {"ok", "okay"}:
                    free(P.fill("Okay! Ask me anything about food, meals or {programme}."))
                elif re.match(r"(?:bye|goodbye|good bye|see you|take care|good night)", u.cleaned):
                    free(P.fill("Take care! Come back any time with a question about food, meals or {programme}."))
                else:
                    free(P.fill("Hello! Ask me anything about food, meals or {programme}."))
            elif block == "definition":
                d.lines.append(lock("T", P.fill("I can tell you how much protein, carbohydrate, fat, fibre or calories a food or dish has, for example \"protein in dal\". "
                                                "For what they mean for your health, please ask {coach} or dietitian.")))
            elif block == "phrase_compare":
                self._compare_phrases(plan, u, d, lock, given, person)
            elif block == "portion":
                d.lines.append(lock("T", P.text("portion")))
            elif block == "reading":
                d.lines.append(lock("T", f"Thank you for sharing your reading of {plan.reading:g} mg/dL. I can't judge a reading or change your care. "
                                         "Please talk to your doctor or dietitian about it."))
            elif block == "about":
                d.lines.append(lock("T", P.fill("I'm {assistant}, an automated helper, not a doctor and not a person. I can look up the nutrition of foods and dishes, "
                                                "suggest dishes from what you have, find dishes near an amount you want, and explain how the programme works. "
                                                "For medical advice please talk to your doctor.")))
            elif block == "body_info":
                d.lines.append(lock("T", "Thank you. I can't work out calorie needs, a target weight or a plan from body measurements; please talk to your doctor or "
                                         "dietitian about that. I can still look up foods and dishes for you."))
            elif block == "special_diet":
                d.gaps.append({"topic": "special diet (fasting, keto)", "reason": "no approved guidance"})
                d.lines.append(lock("T", P.fill("I don't have guidance for fasting or special diets such as keto yet. "
                                                "Please ask {coach} how to fit the programme with what you follow.")))
            elif block == "plan_refusal":
                d.lines.append(lock("T", P.text("plan_refusal")))
            elif block == "metric_refusal":
                d.lines.append(lock("T", P.text("metric_refusal")))
            elif block == "safety_ack":
                d.lines.append(lock("T", "Thank you for telling me. I've noted it and I'll keep it in mind. Please talk to your doctor before you change when you eat. "
                                         "You can ask me about any food or meal."))
            elif block == "profile_ack":
                for line in self._profile_ack(plan.statement):
                    d.exclusion_texts.append(line)                 # it may name a food the person has just said they leave out
                    d.lines.append(lock("T", line))
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
            elif block == "target_meals":
                self._target_meals(plan, d, lock, person, state, u)
            elif block == "explain_last":
                for line in (state.last_explain or {}).get("lines", []):
                    d.lines.append(lock("T", line))
            elif block == "more_unclear":
                d.lines.append(lock("T", "I'm not sure what to show more of. Tell me what you'd like, for example high protein dishes, fewer calories, or what you have at home."))
            elif block == "out_of_scope":
                free(P.fill("I can only help with food, meals and {programme}. Is there something about your meals I can help with?"))
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
                d.lines.append(P.fill("**{meals_heading}:**"))
                for line in self.s.protocol_full():
                    d.lines.append("- " + lock("P", line))
            elif block == "no_meal_rules":                    # a programme without a meal order: said, never filled in
                d.lines.append(lock("T", P.text("no_meal_rules")))
            elif block == "protocol_reminder":
                d.lines.append(lock("P", self.s.protocol_reminder()))
            elif block == "protocol_amounts":
                for step in self.s.golden.steps():
                    d.lines.append(f"- **{_cap(step)}:** " + lock("P", self.s.protocol_amount(step, blocked)))
            elif block == "protocol_amount":
                amount = self.s.protocol_amount("sprouts", blocked)
                if amount:
                    d.lines.append("**For the sprouts step:** " + lock("P", amount))
            elif block == "meal_place":
                if self.s.meal_place():
                    d.lines.append("**In your meal:** " + lock("P", self.s.meal_place()))
            elif block == "between_meals":
                if plan.frequency_note and P.texts.get("frequency_note"):
                    d.lines.append(lock("T", P.text("frequency_note")))
                d.lines.append("**Between meals:** " + lock("P", self.s.between_meals() or P.text("between_meals_none")))
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
                        parts.append(" · " + fact_line(fact, fid))
                    d.lines.append("".join(parts))
            elif block == "food_position":
                for fid in plan.focus_foods:
                    name(fid)
                    if fid in blocked:
                        self._blocked_line(fid, person, d, lock)
                    else:
                        text, pos = self.s.position_for(fid, person.status if person else None)
                        d.lines.append(lock("T", text or P.text("position_none", food=name(fid))))
                        if pos is not None and pos.allowed in ("depends_on_status", "no"):
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
                        d.lines.append(fact_line(fact, fid))
                    elif not group:
                        d.gaps.append({"topic": f"nutrition numbers: {name(fid)}", "reason": "no reliable value"})
                        d.lines.append(lock("T", f"We don't have reliable nutrition numbers for {name(fid)} yet."))
            elif block == "lookup_facts":
                self._lookup_lines(plan, u, d, given, fact_line, name, person, lock)
            elif block == "unknown_food":
                self._unknown_food_block(u, plan, d, lock, given, person)
            elif block == "tip":
                keys = list(plan.focus_foods) + ([plan.topic] if plan.topic == "sabji" else [])
                for text, facts, _tip_id in self.s.tips(keys, given, blocked=blocked):
                    filled = text
                    for ph_key, fact in zip(("A", "B"), facts):
                        filled = filled.replace("{{" + ph_key + "}}", f"{fact.text}{_src(fact)}")
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
            elif block == "status_position" and self.s.golden.uses("status_food_rule"):
                pos = self.s.golden.fruit_position(person.status if person else Status())
                d.lines.append(lock("T", pos.text))
            elif block == "struggle":
                self._struggle_lines(u, d, lock)
            elif block == "boundary":
                d.lines.append(lock("T", "Your doctor or dietitian decides what's right for you personally, including any medicines."))
            elif block == "profile_note":
                shown = set(plan.focus_foods) | set(plan.ingredients) | set(u.foods) | d.foods_named
                for _rule, line in (self.cautions.lines(person, shown, name, u.cleaned) if self.cautions else []):
                    d.lines.append(lock("T", line))         # a condition, medicine or pregnancy caution: fixed wording, never a ban
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
        parts += ["pregnancy"] if person.pregnant else []
        parts += [f"your medicines ({_join([m for m in person.medicines if isinstance(m, str)])})"] if person.medicines else []
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
                filled = filled.replace("{{" + ph_key + "}}", f"{fact.text}{_src(fact)}")
                d.facts.append(fact)
                given.add(f"{fact.record_id}:{fact.field}")
            d.lines.append("- " + lock("K", filled))

    # ---------- recipes (stage 6d) ----------
    def _recipe_block(self, plan, state, u, person, d: Draft, lock, free, name, given: set) -> None:
        reply = bool(plan.reply_slot)
        if plan.time_wish:
            d.lines.append(lock("T", "I don't have cooking times for these dishes yet, so they aren't picked by speed."))
        if person is None:                                  # no profile at all: allergies unknown, so the most careful rules apply
            from .person import Person
            person = Person()
        out = self.recipes.run(person=person, have=list(plan.ingredients), dish_ids=list(plan.recipe_ids),
                               unknown_words=[] if reply else list(u.unknown_words),
                               suggestion=None if reply else u.suggestion, wants_list=plan.wants_list,
                               home_foods=list(state.home_foods), exclude=list(plan.exclude_recipes), exclude_foods=list(plan.exclude_foods),
                               avoid_drafts=list(state.drafts_shown) if plan.exclude_recipes else [])
        d.recipe_trace = out.trace
        general = None
        if plan.unknown_dish and not plan.reply_slot and out.status in {"tips_only", "choices"}:
            # A dish we don't hold: the closest recipe we have, then an AI draft recalculated and gated, then the general fallback.
            closest = self._closest_recipe(plan.unknown_dish, person, d)
            if closest is not None:
                d.unknown_items.append({"word": plan.unknown_dish, "kind": "dish", "suggested_food_id": ""})
                d.backlog_request = True
                d.lines.append(lock("T", f"I don't have a recipe called \"{plan.unknown_dish}\" yet; I've passed it to our dietitian. "
                                         f"The closest dish in our recipes is {closest.name}:"))
                self._render_recipe(closest, plan, d, lock, free, name, state)
                return
            drafted = self.recipes.draft_for_dish(plan.unknown_dish, person)
            d.recipe_trace += drafted.trace
            if drafted.status == "recipe":
                d.unknown_items.append({"word": plan.unknown_dish, "kind": "dish", "suggested_food_id": ""})
                d.backlog_request = True
                d.lines.append(lock("T", f"I don't have a checked recipe for \"{plan.unknown_dish}\" yet; I've passed it to our dietitian. "
                                         "Our AI drafted one from our food list, and our checks recalculated it from USDA values:"))
                self._render_recipe(drafted.recipe, plan, d, lock, free, name, state)
                return
        if plan.unknown_dish and not plan.reply_slot:
            d.unknown_items.append({"word": plan.unknown_dish, "kind": "dish", "suggested_food_id": ""})
            general = self._try_general(plan.unknown_dish, person, d) if out.status in {"tips_only", "choices"} else None
            meanwhile = " Meanwhile, here is a general recipe:" if general is not None and general.status == "recipe" else ""
            d.lines.append(lock("T", f"I don't have a checked recipe for \"{plan.unknown_dish}\" yet; I've passed it to our dietitian.{meanwhile}"))
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
        if general is not None and out.status in {"tips_only", "choices"}:
            if general.status == "recipe":                   # the dish asked for by name comes first; the approved list is not offered instead
                self._render_general(general.recipe, d, lock)
                return
            self._general_declined(general, d, lock)
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
            self._remember_shown(state, [r.recipe_id for r in out.choices])
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
        if plan.more_request:
            d.lines.append(lock("T", "Those are all the checked dishes I have for these foods so far. Here is what helps most:"))
        else:
            d.lines.append(lock("T", "We don't have a checked recipe for this yet, so here is what helps most:"))
        self._tips_lines(plan, state, d, lock, given)

    def _target_meals(self, plan, d: Draft, lock, person, state=None, u=None) -> None:
        if not plan.targets and not plan.target_include and not plan.target_diet:           # "start over", or the last wish taken off (chips on the chat page)
            if state is not None:
                state.target_shown = []
            d.lines.append(lock("T", "Okay, I've cleared that search. Tell me what you'd like, for example high protein or fewer calories."))
            return
        asked = [(t.field, t.value, t.mode) for t in plan.targets]
        parts = []
        every_about = bool(asked) and all(mode == "about" for _f, _v, mode in asked)
        for fld, value, mode in asked:
            if mode in {"high", "low"}:
                parts.append(f"{'high' if mode == 'high' else 'low'} {QUAL_NOUN[fld]}" if fld != "energy_kcal" else ("few calories" if mode == "low" else "many calories"))
                continue
            if mode == "range" and next(t.high for t in plan.targets if t.field == fld) is not None:
                high = next(t.high for t in plan.targets if t.field == fld)
                parts.append(f"{value:g} to {high:g} {LABEL_FOR[fld]}")
                continue
            word = "" if every_about else {"max": "up to ", "min": "at least "}.get(mode, "about ")
            parts.append(f"{word}{value:g} {LABEL_FOR[fld]}")
        parts.extend(sorted(plan.target_include))                  # "dishes with chicken", "dishes with high protein and chicken"
        want = ("about " if every_about else "") + _join(parts)
        if u is not None and re.search(r"\bsnacks?\b", u.cleaned):
            d.lines.append(lock("T", "The programme has no snacks between meals, so these are dishes for your meals."))
        profile_diet = getattr(person, "diet_type", "unknown") if person is not None else "unknown"
        narrower = {"non_vegetarian": {"vegetarian", "jain", "egg"}, "egg": {"vegetarian", "jain"}, "unknown": {"vegetarian", "jain", "egg"}}
        if plan.target_diet and plan.target_diet in narrower.get(profile_diet, set()):
            import dataclasses
            person = dataclasses.replace(person, diet_type=plan.target_diet) if person is not None else Person(diet_type=plan.target_diet)
            profile_diet = plan.target_diet                                  # a narrower diet asked for in this search is honoured
        if u is not None and (getattr(u, "preference", "") == "non_vegetarian" or plan.target_include) and profile_diet in {"vegetarian", "jain"}:
            d.lines.append(lock("T", f"Your profile says {profile_diet}, so I only looked at {profile_diet} dishes."))
        only_nonveg = plan.target_diet == "non_vegetarian" and profile_diet == "non_vegetarian"
        general_shown = False
        if plan.meat_recipe and plan.target_mode == "new" and self.dishes is not None:
            general = self._try_general(f"{_join(sorted(plan.target_include))} recipe", person, d)
            if general is not None and general.status == "recipe":
                d.backlog_request, general_shown = True, True
                d.lines.append(lock("T", f"I don't have an approved {_join(sorted(plan.target_include))} recipe yet, so I've passed your request to our dietitian. "
                                         "Here is a general recipe meanwhile:"))
                self._render_general(general.recipe, d, lock)
            elif general is not None:
                self._general_declined(general, d, lock)
        diet_only = not plan.targets and not plan.target_include
        adjective = {"non_vegetarian": "non-veg", "vegetarian": "vegetarian", "jain": "Jain", "egg": "egg"}.get(plan.target_diet, "") if (only_nonveg or diet_only) else ""
        kind = f"{adjective} dish".strip()                           # "Non-veg dishes with high protein and few calories:"
        with_want = f" with {want}" if want else ""
        if self.dishes is None:
            d.lines.append(lock("T", "I can't look dishes up by their nutrients here yet."))
            return
        if plan.target_mode != "more" and state is not None:
            state.target_shown = []                                # a changed search starts again; "what else" keeps going
        shown_before = list(state.target_shown) if state is not None else []
        left = [w for w in plan.target_exclude]
        matches, close = TargetSearch(self.dishes, self.allergy, self.index).search(plan.targets, person, d.blocked_foods, leave_out=left, skip_codes=shown_before,
                                                                                    drinks=wants_drinks(u.cleaned if u is not None else ""), only_nonveg=only_nonveg,
                                                                                    include=plan.target_include)
        without = f", without {_join(sorted({w for w in left if len(w) > 2})[:4])}" if left else ""
        relaxed = False
        if not matches and plan.target_include and plan.target_mode != "more" and plan.targets and all(t.mode in {"high", "low"} for t in plan.targets):
            # "few calories" with chicken: no chicken dish passes the line the rules draw, so show the nearest and say so
            matches, close = TargetSearch(self.dishes, self.allergy, self.index).search(plan.targets, person, d.blocked_foods, leave_out=left, skip_codes=shown_before,
                                                                                        drinks=wants_drinks(u.cleaned if u is not None else ""), only_nonveg=only_nonveg,
                                                                                        include=plan.target_include, relax=True)
            relaxed = bool(matches)
        if not matches:
            if plan.target_mode == "more" and shown_before:
                d.lines.append(lock("T", f"Those are all the dishes I have near {want}{without}. Try a different amount, or ask me about a dish by name."))
            else:
                d.lines.append(lock("T", f"I couldn't find a checked {kind}{with_want}{without} that suits you. Try a different amount, or ask me about a dish by name."))
                if person is None or person.allergies is None:
                    d.lines.append(lock("T", "I don't know your allergies yet, so I left out dishes with common allergens. Tell me if you have none and I can show more."))
            return
        if state is not None:
            state.target_shown = (state.target_shown + [m.code for m in matches])[-60:]
        if plan.meat_recipe and plan.target_mode == "new":
            d.backlog_request = True                                 # the dietitian sees the request for the backlog
            d.lines.append(lock("T", "These dishes from our library have it:" if general_shown else
                                f"I don't have an approved {_join(sorted(plan.target_include))} recipe yet, so I've passed your request to our dietitian. "
                                "Meanwhile, these dishes from our library have it:"))
        if plan.extra_foods:
            d.lines.append(lock("T", f"I picked these by {_join(sorted(plan.target_include))} only; I can't pick dishes by {_join(plan.extra_foods)} as well yet."))
        if relaxed:
            d.lines.append(lock("T", f"No {kind} with {_join(sorted(plan.target_include))} counts as {_join(parts[:len(plan.targets)])} by our checks. These come closest:"))
        else:
            d.lines.append(lock("T", (f"{kind.capitalize()}es{with_want}{without}" + (", lightest first:" if not plan.targets and not want else ":")) if close else f"No {kind} matches {want}{without} closely. These are the nearest:"))
        for m in matches:
            res = m.result
            quantity, _, unit = res.serving_text.partition(" ")
            serving = f"per {unit}" if quantity in {"1", "1.0"} and unit else f"per {res.serving_text}"
            pieces = []
            fields = [t.field for t in plan.targets]
            if all(t.mode in {"high", "low"} for t in plan.targets) and "energy_kcal" not in fields:
                fields.append("energy_kcal")                                   # a wish for protein also shows what the dish costs in calories
            for fld in fields:
                value = res.per_serving[fld]
                text = f"{value:.0f} {LABEL_FOR[fld]}" if fld == "energy_kcal" else f"{value:.1f}".rstrip("0").rstrip(".") + f" {LABEL_FOR[fld]}"
                pieces.append(text)
                d.facts.append(Fact(f"DISH-{m.code}", fld, float(value), f"about {text} {serving}", serving, "Swastham library", m.name))
            shown = f"about {_join(pieces)} {serving}"
            d.lines.append("- " + m.name.split(" (")[0] + ": " + lock("F", shown))
        d.lines.append(lock("T", "These are the values stored for each dish, one serving as shown. They are not a meal plan, and amounts change with how it is cooked."))

    def _closest_recipe(self, request: str, person, d: Draft):
        """The most similar recipe we hold that passes this person's gate, or None (trace kept for the audit log)."""
        candidates = self.recipes.similar(request, person)
        if not candidates:
            d.recipe_trace.append(f"similar: nothing close to '{request[:40]}'")
            return None
        out = self.recipes.run(person=person, have=[], dish_ids=candidates[:1], unknown_words=[], suggestion=None,
                               wants_list=False, home_foods=[])
        for rid in candidates[1:]:
            if out.status == "recipe":
                break
            out = self.recipes.run(person=person, have=[], dish_ids=[rid], unknown_words=[], suggestion=None,
                                   wants_list=False, home_foods=[])
        d.recipe_trace.append(f"similar: '{request[:40]}' -> {candidates}: " +
                              (f"{out.recipe.recipe_id} passes" if out.status == "recipe" else "none passes for this person"))
        return out.recipe if out.status == "recipe" else None

    def _try_general(self, request: str, person, d: Draft):
        """The general recipe fallback (development only): returns its outcome, or None when it is not available."""
        if self.general is None:
            return None
        from .person import Person
        out = self.general.run(request, person if person is not None else Person(), d.blocked_foods)
        if out.status == "skipped" and out.reasons != ["allergies"]:
            return None
        d.general = {"request": out.request, "status": out.status, "attempts": out.attempts, "trace": out.trace,
                     "reasons": out.reasons[:4], "shown": False}
        return out

    def _render_general(self, recipe, d: Draft, lock) -> None:
        d.general.update({"shown": True, "name": recipe.name, "method": recipe.method, "ingredients": recipe.ingredients,
                          "steps": recipe.steps})
        d.lines.append("**" + lock("T", recipe.name) + "**")
        d.lines.append(lock("T", recipe.label))
        d.lines.append("**You need:**")
        for item in recipe.ingredients:
            d.lines.append("- " + lock("K", item))
        d.lines.append("**How:**")
        for step in recipe.steps:
            d.lines.append("- " + lock("K", step))

    def _general_declined(self, out, d: Draft, lock) -> None:
        if out.reasons == ["allergies"]:
            d.lines.append(lock("T", "I don't know your allergies yet, so I haven't written a general recipe. "
                                     "Tell me any you have, or say none, and ask again."))
        elif out.reasons:
            text = f"I can't write a general recipe for this: {out.reasons[0]}."
            d.exclusion_texts.append(text)
            d.lines.append(lock("T", text))

    @staticmethod
    def _remember_shown(state, recipe_ids) -> None:
        if state is not None:
            for rid in recipe_ids:
                if rid not in state.recipes_shown:
                    state.recipes_shown.append(rid)
            del state.recipes_shown[:-60]

    def _render_recipe(self, recipe, plan, d: Draft, lock, free, name, state=None) -> None:
        d.recipe = recipe
        if recipe.origin == "ai_suggested" and state is not None and recipe.name not in state.drafts_shown:
            state.drafts_shown = (state.drafts_shown + [recipe.name])[-10:]       # "something else" next: the AI must draft a different dish
        if recipe.origin == "approved":
            self._remember_shown(state, [recipe.recipe_id])
            if state is not None:                  # "how many calories in it?" / "and protein?" next: about this dish
                state.last_subject, state.last_subject_kind, state.last_subject_turn = clean(recipe.name), "recipe", state.turn
        if not recipe.also_need and state is not None and state.ingredients_on_hand and recipe.origin == "approved" and plan.recipe_ids:
            # A dish picked from a list for what the person has: say what else it needs (never a food they cannot have).
            have = self.index.with_generics(set(state.ingredients_on_hand))
            recipe.also_need = [f for f in self.recipes.recipes[recipe.recipe_id]["main_ingredients"] if f not in have and f not in d.blocked_foods]
        for item in recipe.ingredients:
            d.foods_named.add(item["food"])
        if getattr(recipe, "book", None):
            d.foods_named.update(self.recipes.step_foods(recipe))     # the book's own steps ("serve with chapati"), already gated
        used = [f for f in plan.ingredients if f in {i["food"] for i in recipe.ingredients} and f not in d.blocked_foods
                and self.index.foods[f]["category"] != "pantry"] if not plan.recipe_ids else []
        if used:                                   # only foods that really are in the recipe, never words from the message
            free(f"With {_join([name(f) for f in used])} you can make this:")
        elif plan.recipe_ids:
            free("Here's the recipe:")
        d.lines.append("**" + lock("T", recipe.name) + "**")
        recipe.label = self.recipes.label_for(recipe)          # after any repair or scaling: an adapted dish is no longer "as recommended"
        d.lines.append(lock("T", recipe.label))
        d.lines.append(lock("T", f"**You need** ({recipe.makes}):"))       # locked: the AI must not hint at portions
        for line in self.recipes.ingredient_lines(recipe):
            d.lines.append("- " + lock("K", line))
        if recipe.assumed:                         # the book left these open: the grams we counted are named, never hidden
            d.lines.append("**Amounts we assumed:** " + lock("T", _join(
                [f"{a['text']} {_grams(a['grams'])} " + ("(the book gives no amount)" if a["book"] == "no amount" else f"(the book says \"{a['book']}\")")
                 for a in recipe.assumed])))
        if recipe.trace_items:
            d.lines.append("**Not counted (a pinch or a few leaves):** " + lock("T", _join(recipe.trace_items)))
        if recipe.optional_items:
            d.lines.append("**Optional, not counted:** " + lock("T", _join(recipe.optional_items)))
        d.lines.append("**How:**")
        for step in recipe.steps:
            d.lines.append("- " + lock("K", step))
        totals = self.recipes.totals_text(recipe)
        if totals:
            d.lines.append("**Whole dish:** " + lock("F", totals))
        each = self.recipes.per_unit_text(recipe)
        if each:
            d.lines.append(lock("F", each))
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
            return "", programme().text("choice_intro")
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
        if plan.type in {"protocol_explain", "meal_protocol_application"} and not programme().uses("meal_order"):
            return "", ""                                # nothing to introduce: the next line says the programme has no meal rules
        if plan.type == "protocol_explain":
            return programme().text("explain_intro"), ""
        if plan.type == "meal_protocol_application":
            return "Here's what the programme says about meal timing:", ""
        if plan.type == "support_struggle":
            if SYMPTOM_RE.search(u.cleaned) and not re.search(r"\b(?:hungry|hunger|craving|cravings)\b", u.cleaned):
                return "I'm sorry you're not feeling well.", ""
            return "That's common, especially in the first weeks.", ""
        return "", ""

    def _lookup_lines(self, plan, u, d, given, fact_line, name, person=None, lock=None) -> None:
        field = "energy_kcal"
        for word, fld in (("carb", "carbohydrate_g"), ("protein", "protein_g"), ("fat", "fat_g"), ("fiber", "fiber_g"), ("sugar", "sugars_g"),
                          ("sodium", "sodium_mg")):
            if re.search(rf"\b{word}s?\b", u.cleaned):
                field = fld
        recipe = self.recipes.nutrition_of(u.recipe_nutrients, person or Person()) if (u.recipe_nutrients and self.recipes) else None
        if recipe is not None and self._dish_named_not_recipe(recipe, u, plan):
            recipe = None       # "calories in pizza": a book's other name for its Sprout Base Pizza; the pizza record answers
        if recipe is not None:                      # one of our recipes: its own totals, worked out from its ingredients
            totals, each = self.recipes.totals_text(recipe), self.recipes.per_unit_text(recipe)
            if totals:
                d.lines.append(lock("T", f"For our {recipe.name} recipe ({recipe.makes}):") + " " + lock("F", totals))
                if each:
                    d.lines.append(lock("F", each))
            else:
                d.gaps.append({"topic": f"nutrition numbers: {recipe.name} recipe", "reason": "no reliable value"})
                d.lines.append(lock("T", f"We don't have reliable nutrition numbers for our {recipe.name} recipe yet."))
            return
        if field in {"sugars_g", "sodium_mg"} and len(plan.focus_foods) > 1:           # "sugar in mango": sugar is what is asked, not a second food
            word = "sugar" if field == "sugars_g" else "sodium"
            plan.focus_foods = [f for f in plan.focus_foods if word not in clean(self.index.foods[f]["name"]).split()] or plan.focus_foods
        hits = self._dish_hits(u, plan) if self.dishes is not None else []
        if plan.focus_foods:
            for fid in plan.focus_foods:
                if fid in d.blocked_foods:
                    self._blocked_line(fid, person, d, lock)
                    continue
                fact = self.s.pick_fact(fid, set(), fields=(field,))
                if fact:
                    grams = grams_in(u.cleaned)
                    d.lines.append(fact_line(scaled_fact(fact, grams) if grams else fact, fid))
                else:
                    d.gaps.append({"topic": f"{field.split('_')[0]}: {name(fid)}", "reason": "no reliable value"})
                    d.lines.append(f"We don't have a reliable {field.split('_')[0]} value for {name(fid)} yet.")
            first = self.index.foods[plan.focus_foods[0]]
            per100 = getattr(d.facts[-1], "serving", "") == "per 100 g" if d.facts else False
            if per100 and first.get("unit_weight_g") is None and not grams_in(u.cleaned) and not hits:
                aliases = "|".join(re.escape(clean(a)) for a in [first["name"], *first.get("aliases", [])])
                if re.search(rf"\b(?:\d+|one|two|three|four|five|six|a|an)\s+(?:small |medium |large |big )?(?:{aliases})s?\b", u.cleaned):
                    d.lines.append(lock("T", "We don't have a reliable weight for one piece yet, so I can't give a per-piece number; the figure above is per 100 g."))
                elif measure_in(u.cleaned):
                    d.lines.append(lock("T", f"We don't have a reliable weight for a {measure_in(u.cleaned)} of {first['name']} yet, so the figure above is per 100 g."))
            if hits:
                self._dish_lines(hits, d, lock, given, person, field)   # a dish named next to foods is answered too, never dropped
            return
        if hits and self._dish_lines(hits, d, lock, given, person, field):
            return
        self._unknown_phrase_lines(u, field, d, lock, given, person)

    # ---------- foods that are not on the approved list: USDA facts for information, never a guess ----------
    def _library_search(self) -> LibrarySearch:
        if getattr(self, "_lib_search", None) is None:
            self._lib_search = LibrarySearch(self.s.library, bad_mapping_pairs(self.s.data.black_lists))
        return self._lib_search

    def _usda_exact(self, span: str) -> bool:
        """Does the USDA library have a plain record for this word (so "oats" is the grain, not "oats burfi", and "apple" is the fruit)?"""
        search = self._library_search()
        queries = {span, span + "s", span.rstrip("s")}
        for query in queries:
            for hit in search.search(query, limit=12, usda_only=True):
                name = clean(hit["food_name"])
                words = name.split()
                if name and words[0] in queries and set(words) & {"raw", "whole", "plain", "fresh"}:      # the plain food: "Apples, raw", "Oats, raw", "Milk, whole"
                    return True
        return False

    def _phrase_block(self, phrase: str, person) -> str | None:
        """Why a food named only in words cannot be offered to this person (diet or allergy), or None."""
        words = set(clean(phrase).split())
        diet = getattr(person, "diet_type", "unknown") if person is not None else "unknown"
        if (words & MEAT_FISH and diet in {"vegetarian", "jain", "egg"}) or (words & EGG and diet in {"vegetarian", "jain"}):
            return f"{_cap(phrase)} won't suit you because of your {'vegetarian' if diet != 'jain' else 'jain'} diet. Please check with your doctor or dietitian."
        if person is not None and self.allergy is not None:
            wanted = set(person.allergies or [])
            if person.unmapped_allergies:
                wanted |= set(self.allergy.map["major"])
            hit = sorted(set(self.allergy.read_reply(phrase) or []) & wanted)
            if hit:
                return f"{_cap(phrase)} won't suit you because of your {self.allergy.label(hit[0])} allergy. Please check with your doctor or dietitian."
        return None

    def _name_ok(self, person):
        """A test for USDA record names: False when the name holds meat, fish or egg the diet excludes, or an allergen the person has."""
        diet = getattr(person, "diet_type", "unknown") if person is not None else "unknown"
        banned = (MEAT_FISH | EGG if diet in {"vegetarian", "jain"} else MEAT_FISH if diet == "egg" else set())
        wanted = set(person.allergies or []) if person is not None else set()
        if person is not None and person.unmapped_allergies and self.allergy is not None:
            wanted |= set(self.allergy.map["major"])

        def ok(name: str) -> bool:
            if banned & set(clean(name).split()):
                return False
            return not (wanted and self.allergy is not None and set(self.allergy.read_reply(name) or []) & wanted)
        return ok

    def _usda_fact_lines(self, phrase: str, field: str, d, lock, given, grams, limit: int = 3, person=None) -> bool:
        facts = usda_hits(self._library_search(), self.s.library, phrase, field, make_fact, limit, self._name_ok(person))
        for fact in facts:
            fact = scaled_fact(fact, grams) if grams else fact
            d.facts.append(fact)
            given.add(f"{fact.record_id}:{fact.field}")
            d.lines.append(lock("F", f"{fact_sentence(fact)}{_src(fact)}"))
        if len(facts) > 1:
            d.lines.append(lock("T", f"There are several kinds of {phrase}; tell me which one you mean for a closer answer."))
        return bool(facts)

    def _unknown_phrase_lines(self, u, field: str, d, lock, given, person) -> None:
        phrase = food_phrase(u.focus or u.cleaned)
        if not phrase:
            d.lines.append("I couldn't find that food in our data yet. Could you tell me its name another way?")
            return
        blocked = self._phrase_block(phrase, person)
        if blocked:
            d.exclusion_texts.append(blocked)
            d.lines.append(lock("T", blocked))
            return
        if self._usda_fact_lines(phrase, field, d, lock, given, grams_in(u.cleaned), person=person):
            return
        words = [w for w in phrase.split() if w not in COLOR_WORDS] or phrase.split()
        if len(words) > 1 or words != phrase.split():
            for word in words[:3]:                                   # "poha upma": each food; "black coffee": the main word
                blocked_word = self._phrase_block(word, person)
                if blocked_word:
                    d.exclusion_texts.append(blocked_word)
                    d.lines.append(lock("T", blocked_word))
                    continue
                if self.dishes is not None:
                    result = self.dishes.resolve(word)
                    if result.status in {FOUND, AMBIGUOUS} and word in self.dishes.exact and self._dish_lines([(word, result)], d, lock, given, person, field):
                        continue
                if self._usda_fact_lines(word, field, d, lock, given, grams_in(u.cleaned), 2, person):
                    continue
            if any(line for line in d.lines):
                return
        d.unknown_items.append({"word": phrase[:60], "kind": "ingredient", "suggested_food_id": ""})
        d.backlog_request = True
        d.lines.append(lock("T", f"I couldn't find \"{phrase}\" in our data yet; I've passed it to our dietitian. Could you tell me its name another way?"))

    def _unknown_food_block(self, u, plan, d, lock, given, person) -> None:
        """"Can I eat pizza?", "is coffee ok?": a food that is not on the approved list. Said plainly, queued, with facts for information only."""
        phrase = u.unknown_food_query or subject_of(u.cleaned) or food_phrase(u.cleaned)
        if not phrase:
            d.lines.append(lock("T", "I couldn't find that food in our data yet. Could you tell me its name another way?"))
            return
        blocked = self._phrase_block(phrase, person)
        if blocked:
            d.exclusion_texts.append(blocked)
            d.lines.append(lock("T", blocked))
            return
        d.unknown_items.append({"word": phrase[:60], "kind": "ingredient", "suggested_food_id": ""})
        d.backlog_request = True
        d.lines.append(lock("T", f"I can't say whether {phrase} fits the programme yet: it isn't on our approved food list. I've passed it to our dietitian."))
        shown = False
        if self.dishes is not None:
            result = self.dishes.resolve(phrase)
            if result.status == FOUND and phrase in self.dishes.exact and not self._dish_allergen(result.code or "", person)                     and not self._diet_blocks_dish(result.code or "", result.name or phrase, person):           # only a dish with exactly this name; never a loose match
                shown = self._dish_lines([(phrase, result)], d, lock, given, person, "energy_kcal")
        if not shown:
            shown = self._usda_fact_lines(phrase, "energy_kcal", d, lock, given, None, 2, person)
        if shown:
            d.lines.append(lock("T", "These are facts for information only, not permission."))

    DISH_FIELDS = {"energy_kcal": "energy_kcal", "carbohydrate_g": "carb_g", "protein_g": "protein_g", "fat_g": "fat_g", "fiber_g": "fibre_g"}

    def _dish_named_not_recipe(self, recipe, u, plan) -> bool:
        """The message names our recipe only by another name ("pizza" for Sprout Base Pizza, "dalma" for Mixed vegetable with dal)
        and the dish data holds a dish of exactly that name: the dish is what was asked. "calories in aloo gobhi" still gets
        our Aloo Gobhi (dry), because it names the recipe itself."""
        if self.dishes is None or f" {clean(recipe.name.split(' (')[0])} " in f" {u.cleaned} ":
            return False
        return any(result.status == FOUND and span in self.dishes.exact for span, result in self._dish_hits(u, plan))

    def _dish_hits(self, u, plan) -> list:
        """The dishes the message names, leaving out words that are only a food from the food list ("dal" is the food, not a dish)."""
        hits = find_all_in_text(self.dishes, u.focus or u.cleaned)
        aliases = [set(clean(a).split()) for fid in plan.focus_foods
                   for a in [self.index.foods[fid]["name"], *self.index.foods[fid].get("aliases", [])]]
        kept = [(span, result) for span, result in hits if not any(set(span.split()) <= words for words in aliases)]
        # one loose word ("oats", "apple") that is a plain food in USDA is that food, not the first dish that happens to contain the word
        return [(span, result) for span, result in kept if not (len(span.split()) == 1 and span not in self.dishes.exact and self._usda_exact(span))]

    def _dish_lines(self, hits: list, d, lock, given, person, field: str) -> bool:
        """Lines for every dish a nutrient question names. Returns False when there is nothing to say about any of them."""
        said = False
        most = int(self.dishes.rules.get("max_dishes_answered", 3))
        for span, result in hits[:most]:
            said |= self._one_dish(span, result, d, lock, given, person, field)
        if said and len(hits) > most:
            d.lines.append(lock("T", f"I can look up {most} dishes at a time. Ask me about the others in a new question."))
        return said

    def food_exists(self, phrase: str) -> bool:
        """Is this a dish or a food we hold data for (so a bare "jalebi" is worth a lookup)? Used only to tell a food name from noise."""
        for word in [phrase, *phrase.split()]:
            if self.dishes is not None and self.dishes.candidates(word)[0]:
                return True
            if self._library_search().search(word, limit=1, usda_only=True) or self._library_search().search(word + "s", limit=1, usda_only=True):
                return True
        return False

    def _compare_phrases(self, plan, u, d, lock, given, person) -> None:
        """Foods side by side, each with its own stored fact for the nutrient asked (calories unless another is named); no verdict is invented."""
        field = "energy_kcal"
        for word, fld in (("carb", "carbohydrate_g"), ("protein", "protein_g"), ("fat", "fat_g"), ("fiber", "fiber_g"), ("sugar", "sugars_g"), ("sodium", "sodium_mg")):
            if re.search(rf"\b{word}s?\b", u.cleaned):
                field = fld
        d.lines.append(lock("T", "Here is how they compare:"))
        servings = set()
        shown = 0
        for phrase in plan.compare_sides:
            blocked = self._phrase_block(phrase, person)
            if blocked:
                d.exclusion_texts.append(blocked)
                d.lines.append("- " + lock("T", blocked))
                continue
            fact = None
            fids = [m.food_id for m in self.index.find(phrase) if clean(phrase) in {clean(a) for a in [self.index.foods[m.food_id]["name"], *self.index.foods[m.food_id].get("aliases", [])]}]
            if fids and fids[0] not in d.blocked_foods:
                fact = self.s.pick_fact(fids[0], set(), fields=(field,))
            if fact is None and self.dishes is not None:
                result = self.dishes.resolve(phrase)
                if result.status == AMBIGUOUS:
                    options = [c["name"] for c in result.candidates if not self._diet_blocks_dish(c.get("code", ""), c["name"], person)]
                    if options:
                        names = [_short_dish(n, options) for n in options]
                        d.lines.append("- " + lock("T", f"There are a few dishes called {phrase}: {_join(names)}. Which one do you mean?"))
                        continue
                if result.status == FOUND and phrase in self.dishes.exact and (self._diet_blocks_dish(result.code or "", result.name or phrase, person)
                                                                              or self._dish_allergen(result.code or "", person)):
                    reason = self._dish_allergen(result.code or "", person)
                    text = (f"{_cap(phrase)} contains {reason}, so it won't suit you." if reason
                            else f"{_cap(phrase)} won't suit you because of your {person.diet_type} diet.")
                    d.exclusion_texts.append(text)
                    d.lines.append("- " + lock("T", text))
                    continue
                if result.status == FOUND and phrase in self.dishes.exact:
                    shown_fact = dish_fact_text(result, self.DISH_FIELDS.get(field, "energy_kcal"))
                    if shown_fact is not None:
                        sentence = plain_sentence(result, self.DISH_FIELDS.get(field, "energy_kcal"), phrase)
                        fact = Fact(f"DISH-{result.code}", field, shown_fact.value, shown_fact.text, shown_fact.serving, "Swastham library", result.name or phrase)
                        d.facts.append(fact)
                        d.lines.append("- " + lock("F", sentence))
                        servings.add(shown_fact.serving)
                        shown += 1
                        continue
            if fact is None:
                found = usda_hits(self._library_search(), self.s.library, phrase, field, make_fact, 1, self._name_ok(person))
                fact = found[0] if found else None
            if fact is None:
                d.lines.append("- " + lock("T", f"I couldn't find {phrase} in our data yet."))
                d.unknown_items.append({"word": phrase[:60], "kind": "ingredient", "suggested_food_id": ""})
                continue
            d.facts.append(fact)
            given.add(f"{fact.record_id}:{fact.field}")
            servings.add(fact.serving)
            d.lines.append("- " + lock("F", f"{fact_sentence(fact, phrase)}{_src(fact)}"))
            shown += 1
        if shown >= 2 and len(servings) > 1:
            d.lines.append(lock("T", "These are for different serving sizes, so the numbers are not side by side per bite."))

    def _profile_ack(self, statement: dict) -> list[str]:
        """The plain acknowledgement of what the person said about themselves (the change itself is made in the pipeline)."""
        lines = []
        if statement.get("label") == "none":
            return ["Noted: no allergies."]
        if statement.get("label") == "vegan":
            lines.append("Noted: vegan. I'll show vegetarian dishes and leave out milk and egg foods. I can't check every hidden animal product, so please read labels.")
        elif statement.get("allergies") and self.allergy is not None:
            labels = _join([self.allergy.label(a) for a in statement["allergies"]])
            lines.append(f"Noted. I'll leave out foods with {labels} from now on in this chat.")
        if statement.get("label") == "diet" and statement.get("diet"):
            readable = {"vegetarian": "vegetarian", "egg": "vegetarian and egg", "jain": "Jain", "non_vegetarian": "vegetarian and non-vegetarian"}
            lines.append(f"Noted: I'll show {readable.get(statement['diet'], statement['diet'])} dishes.")
        if statement.get("avoid"):
            lines.append(f"Noted. I'll leave out {_join([self.index.name(f) for f in statement['avoid']])} from now on in this chat.")
        return lines or ["Noted."]

    @staticmethod
    def _banned_words(person) -> set:
        """Meat, fish and egg words this person's diet leaves out (a vegetarian, a jain; an egg-eater leaves out meat and fish)."""
        diet = getattr(person, "diet_type", "unknown") if person is not None else "unknown"
        return (MEAT_FISH | EGG) if diet in {"vegetarian", "jain"} else MEAT_FISH if diet == "egg" else set()

    def _dish_allergen(self, code: str, person) -> str | None:
        """The allergen (as the person would say it) a dish's ingredient list holds that this person has, or None."""
        if person is None or self.allergy is None or self.dishes is None:
            return None
        wanted = set(person.allergies or [])
        if person.unmapped_allergies:
            wanted |= set(self.allergy.map["major"])
        if not wanted:
            return None
        hit = sorted(set(self.allergy.read_reply(" , ".join(self.dishes.ingredient_names(code))) or []) & wanted)
        return self.allergy.label(hit[0]) if hit else None

    def _diet_blocks_dish(self, code: str, name: str, person) -> bool:
        banned = self._banned_words(person)
        if not banned:
            return False
        words = set(clean(name).split())
        for ingredient in (self.dishes.ingredient_names(code) if self.dishes is not None and code else []):
            words |= set(clean(ingredient).split())
        return bool(words & banned)

    def _one_dish(self, span: str, result, d, lock, given, person, field: str) -> bool:
        blocked = d.blocked_foods

        def blocked_in(name: str) -> str | None:
            hit = sorted({m.food_id for m in self.index.find(name or "")} & blocked)
            return hit[0] if hit else None

        if result.status == AMBIGUOUS:
            shown = [c["name"] for c in result.candidates if not blocked_in(c["name"]) and not self._diet_blocks_dish(c.get("code", ""), c["name"], person)]
            names = [_short_dish(n, shown) for n in shown]
            if names:
                d.lines.append(lock("T", f"I found a few dishes with that name: {_join(names)}. Which one do you mean?"))
                return True
            blocked_text = self._phrase_block(span, person)
            if blocked_text or self._banned_words(person):
                text = blocked_text or f"{_cap(span)} won't suit you because of your {person.diet_type} diet. Please check with your doctor or dietitian."
                d.exclusion_texts.append(text)
                d.lines.append(lock("T", text))
                return True
            return False
        name = (result.name or span).split(" (")[0]
        if result.status == FOUND:
            food = blocked_in(result.name or "")
            if food:
                self._blocked_line(food, person, d, lock)
                return True
            if self._diet_blocks_dish(result.code or "", result.name or span, person):
                text = f"{_cap(name)} won't suit you because of your {person.diet_type} diet. Please check with your doctor or dietitian."
                d.exclusion_texts.append(text)
                d.lines.append(lock("T", text))
                return True
            allergens = set(person.allergies or []) if person else set()
            if person and person.unmapped_allergies and self.allergy:
                allergens |= set(self.allergy.map["major"])         # an allergy we could not map: stay clear of every common one
            if allergens and self.allergy:
                found_in = sorted((set(self.allergy.read_reply(" , ".join(self.dishes.ingredient_names(result.code))) or [])) & allergens)
                if found_in:
                    text = f"{name} contains {self.allergy.label(found_in[0])}, so it won't suit you. Please check with your doctor or dietitian."
                    d.exclusion_texts.append(text)
                    d.lines.append(lock("T", text))
                    return True
            shown = dish_fact_text(result, self.DISH_FIELDS.get(field, "energy_kcal"))
            if shown is None:
                d.gaps.append({"topic": f"{field.split('_')[0]}: dish", "reason": "no reliable value"})
                d.lines.append(lock("T", f"We don't have a reliable value for that in {name} yet."))
                return True
            source = "calculated from ingredients" if result.path == "B" else "Swastham library"
            fact = Fact(f"DISH-{result.code}", field, shown.value, shown.text, shown.serving, source, result.name or name)
            d.facts.append(fact)
            given.add(f"{fact.record_id}:{fact.field}")
            sentence = plain_sentence(result, self.DISH_FIELDS.get(field, "energy_kcal"), name)
            d.lines.append(lock("F", f"{sentence}{_src(fact)}"))
            if shown.estimate:
                d.lines.append(lock("T", "This is an estimate from the recipe, so the real number may be a bit higher or lower."))
            return True
        if blocked_in(result.name or ""):
            name = "that dish"                          # never name a food this person can't have, even to say we have no number
        d.lines.append(lock("T", f"I can't give a reliable number for {name} yet."))
        d.unknown_items.append({"word": span, "kind": "dish", "suggested_food_id": ""})
        return True

    def _struggle_lines(self, u, d, lock) -> None:
        if re.search(r"\b(hungry|hunger|craving|cravings)\b", u.cleaned):
            d.lines.append("**Between meals:** " + lock("P", self.s.between_meals()))
            if self.s.golden.next_meal():
                d.lines.append("**At your next meal:** " + lock("P", self.s.golden.next_meal()))
            d.lines.append(lock("T", "If you feel shaky, sweaty or dizzy, especially if you take diabetes medicine, eat something "
                                     "and contact your doctor."))
        elif SYMPTOM_RE.search(u.cleaned):
            d.lines.append(lock("T", "I can't judge symptoms. If it continues or gets worse, please talk to your doctor."))
            d.lines.append(lock("T", "If you feel shaky, sweaty or dizzy, especially if you take diabetes medicine, eat something and contact your doctor."))
        else:
            d.lines.append(lock("T", "Weight changes slowly and differently for each person. If nothing changes over several weeks, "
                                     + programme().fill("talk to your doctor or {coach_bare}.")))
