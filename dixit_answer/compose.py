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
from .planner import AnswerPlan
from .safety import EMERGENCY_ANSWER, safety_caution
from .services import Services
from .state import ChatState

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z]+\d*)\}\}")


@dataclass
class Draft:
    lines: list = field(default_factory=list)       # markdown lines with placeholders
    locked: dict = field(default_factory=dict)      # placeholder -> text
    facts: list = field(default_factory=list)       # Fact objects used
    foods_named: set = field(default_factory=set)   # food IDs the answer may name
    free_lines: list = field(default_factory=list)  # indexes of lines the AI may reword

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


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


class Composer:
    def __init__(self, services: Services, index: FoodIndex, word_lists: dict):
        self.s = services
        self.index = index
        self.words = word_lists

    def build(self, plan: AnswerPlan, state: ChatState, u) -> Draft:
        d = Draft()
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
            if block == "emergency":
                d.lines.append(lock("T", EMERGENCY_ANSWER))
            elif block == "greeting":
                free("You're welcome! Ask me anything about food, meals or the Dixit programme." if "thank" in u.cleaned
                     else "Hello! Ask me anything about food, meals or the Dixit programme.")
            elif block == "out_of_scope":
                free("I can only help with food, meals and the Dixit programme. Is there something about your meals I can help with?")
            elif block == "opening":
                free("Thank you for asking. Many people work on their weight, and steady changes work best.")
            elif block == "ack":
                free("Thanks, noted.")
            elif block == "direct":
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
                    d.lines.append(f"- **{_cap(step)}:** " + lock("P", self.s.protocol_amount(step)))
            elif block == "protocol_amount":
                d.lines.append("**For the sprouts step:** " + lock("P", self.s.protocol_amount("sprouts")))
            elif block == "meal_place":
                d.lines.append("**In your meal:** " + lock("P", self.s.meal_place()))
            elif block == "between_meals":
                d.lines.append("**Between meals:** " + lock("P", self.s.between_meals()))
            elif block == "choices":
                for group in self.s.choices(plan.topic, state.food_preference):
                    foods = _join([name(f) for f in group.foods])
                    d.lines.append(lock("G", f"**{group.label}:** {foods}"))   # approved wording: locked as a whole
            elif block == "focus_compare":
                for fid in plan.focus_foods:
                    group = self.s.group_of(fid)
                    label = {"choose_more_often": "a fill-up sabji, choose more often",
                             "fine_but_starchy": "starchy: fine, but closer to the chapati part",
                             "choose_less_often": "have less often"}.get(group.direction if group else "", "")
                    # Same field for every food so they line up; a comparison may repeat a number (R11 exception).
                    fact = self.s.pick_fact(fid, set(), fields=("energy_kcal",)) or self.s.pick_fact(fid, set())
                    parts = [f"- **{_cap(name(fid))}**"]
                    if label:
                        parts.append(": " + lock("G", label))
                    if fact:
                        parts.append(" · " + fact_ph(fact))
                    d.lines.append("".join(parts))
            elif block == "food_position":
                for fid in plan.focus_foods:
                    name(fid)
                    d.lines.append(lock("T", self.s.position_for(fid)))
            elif block == "food_facts":
                for fid in plan.focus_foods:
                    group = self.s.group_of(fid)
                    if group and group.direction == "fine_but_starchy":
                        d.lines.append("**Good to know:** " + lock("G", f"{name(fid)} is a starchy vegetable, closer to the chapati or rice part of the meal") + ".")
                    fact = self.s.pick_fact(fid, given)
                    if fact:
                        d.lines.append(f"**{_cap(name(fid))}** · " + fact_ph(fact))
                    elif not group:
                        d.lines.append(lock("T", f"We don't have reliable nutrition numbers for {name(fid)} yet."))
            elif block == "lookup_facts":
                self._lookup_lines(plan, u, d, given, fact_ph, name)
            elif block == "unknown_food":
                d.lines.append(lock("T", "I couldn't find that food in our data yet. Could you tell me its name another way?"))
            elif block == "tip":
                keys = list(plan.focus_foods) + ([plan.topic] if plan.topic == "sabji" else [])
                for text, facts, _tip_id in self.s.tips(keys, given):
                    filled = text
                    for ph_key, fact in zip(("A", "B"), facts):
                        filled = filled.replace("{{" + ph_key + "}}", f"{fact.text} ({_short_source(fact)})")
                        d.facts.append(fact)
                        given.add(f"{fact.record_id}:{fact.field}")
                    d.lines.append("**Tip:** " + lock("K", filled))
            elif block == "tips_list":
                keys = ["recipe", *plan.ingredients, *state.home_foods]
                for text, facts, _tip_id in self.s.tips(keys, given, limit=3):
                    filled = text
                    for ph_key, fact in zip(("A", "B"), facts):
                        filled = filled.replace("{{" + ph_key + "}}", f"{fact.text} ({_short_source(fact)})")
                        d.facts.append(fact)
                        given.add(f"{fact.record_id}:{fact.field}")
                    d.lines.append("- " + lock("K", filled))
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
            elif block == "status_unknown":
                d.lines.append(lock("T", "The programme's rules can change when your diabetes status changes. I can't confirm the rule for "
                                          "your situation yet."))
            elif block == "struggle":
                self._struggle_lines(u, d, lock)
            elif block == "boundary":
                d.lines.append(lock("T", "Your doctor or dietitian decides what's right for you personally, including any medicines."))
            elif block == "followup" and plan.followup:
                d.lines.append(lock("Q", plan.followup["text"]))
        for fid in plan.focus_foods + plan.ingredients + list(u.foods):
            d.foods_named.add(fid)
        return d

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

    def _lookup_lines(self, plan, u, d, given, fact_ph, name) -> None:
        field = "energy_kcal"
        for word, fld in (("carb", "carbohydrate_g"), ("protein", "protein_g"), ("fat", "fat_g"), ("fiber", "fiber_g")):
            if re.search(rf"\b{word}\b", u.cleaned):
                field = fld
        if plan.focus_foods:
            for fid in plan.focus_foods:
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
