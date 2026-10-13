"""Answer planner (stage 5): a fixed rules table turns the understanding and the chat state into an answer plan.

Rules (architecture page, section 6): R1 emergency · R2 caution before timing advice when risk is unknown · R3 doctor-first when
risk is known · R4 food-choice blocks · R5 protocol level · R6 replies · R7 recipes · R8 follow-up ladder · R9 warm opening ·
R10 say what we don't have · R11 no repeated facts (in services) · R12 diabetes status only when it matters.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .state import ChatState
from .understand import Understanding

FOLLOWUPS = {
    "FU-SAFETY": ("safety", "Do you take any medicine for diabetes or blood pressure, or are you pregnant?"),
    "FU-HOME-FOODS": ("home_foods", "Which sabjis are cooked most often at home?"),
    "FU-PREFERENCE": ("food_preference", "Do you eat vegetarian, with eggs, or non-vegetarian?"),
    "FU-SPROUTS": ("sprouts_choice", "For the sprouts step, do you have moong or matki, or would you prefer eggs?"),
    "FU-INGREDIENTS": ("ingredients", "Tell me which vegetables you have at home, and I'll suggest how to cook them."),
}

FOOD_TYPES = {"food_choice_guidance", "food_allowed", "protocol_permission", "neutral_food_comparison"}
RECIPE_TYPES = {"recipe_request", "meal_from_ingredients"}
TIMING_TYPES = {"protocol_explain", "meal_protocol_application"}
WEIGHT_CONDITIONS = {"obesity"}


@dataclass
class AnswerPlan:
    type: str
    blocks: list = field(default_factory=list)
    protocol_level: str = "none"          # full | reminder | meal_place | none
    timing_advice: bool = False
    safety_caution: bool = False
    warm_opening: bool = False
    followup: dict | None = None          # {"id", "slot", "text"}
    focus_foods: list = field(default_factory=list)
    topic: str = "home_meal"
    pairs_with: list = field(default_factory=list)
    ingredients: list = field(default_factory=list)
    reply_slot: str = ""
    rules: list = field(default_factory=list)
    max_words: int = 120

    def as_dict(self) -> dict:
        return asdict(self)


def _choose_followup(plan: AnswerPlan, state: ChatState) -> None:
    """R8: one question, from a fixed ladder; never a slot already filled or a question already asked."""
    asked = set(state.followups_asked)
    ladder: list[str] = []
    if plan.timing_advice and state.safety_asked_turn is None and state.risk_level() == "unknown":
        ladder.append("FU-SAFETY")
    if plan.type in FOOD_TYPES or plan.reply_slot in {"safety", "home_foods"}:
        if not state.home_foods:
            ladder.append("FU-HOME-FOODS")
        if state.food_preference == "unknown":
            ladder.append("FU-PREFERENCE")
    if plan.type == "recipe_request" and not plan.ingredients and not state.ingredients_on_hand:
        ladder.append("FU-INGREDIENTS")
    if plan.type in RECIPE_TYPES or plan.protocol_level in {"full", "meal_place"}:
        ladder.append("FU-SPROUTS")
    for fid in ladder:
        if fid in asked and fid != "FU-SAFETY":
            continue
        if fid == "FU-SAFETY" and state.safety_asked_turn is not None:
            continue
        slot, text = FOLLOWUPS[fid]
        plan.followup = {"id": fid, "slot": slot, "text": text}
        plan.rules.append("R8")
        return


def _protocol_level(plan: AnswerPlan, u: Understanding, state: ChatState) -> None:
    """R5: full when asked for or never explained in a general 'what should I eat'; one line for recipes; else none."""
    explained = state.protocol_explained_turn
    if plan.type in TIMING_TYPES:
        plan.protocol_level = "full"
    elif plan.type == "food_choice_guidance" and u.topic == "home_meal" and explained is None:
        plan.protocol_level = "full"
    elif plan.type in RECIPE_TYPES:
        plan.protocol_level = "meal_place"
    else:
        plan.protocol_level = "none"
    if plan.protocol_level == "full" and explained is not None and plan.type not in TIMING_TYPES \
            and state.turn + 1 - explained <= 3:
        plan.protocol_level = "reminder"
    plan.rules.append("R5")
    plan.timing_advice = plan.protocol_level == "full"


def make_plan(u: Understanding, state: ChatState, emergency: bool = False) -> AnswerPlan:
    plan = AnswerPlan(type=u.type, topic=u.topic, pairs_with=list(u.pairs_with))
    if emergency:
        plan.type, plan.blocks, plan.rules = "emergency", ["emergency"], ["R1"]
        return plan
    if u.type == "greeting_thanks":
        plan.blocks = ["greeting"]
        return plan
    if u.type == "out_of_scope":
        plan.blocks = ["out_of_scope"]
        return plan

    # ---- replies to our last question (R6) ----
    if u.type == "followup_reply":
        slot = next(iter(u.slot_fill))
        plan.reply_slot = slot
        plan.rules.append("R6")
        if slot == "home_foods":
            plan.type = "food_choice_guidance"
            plan.topic = "sabji"
            plan.focus_foods = list(u.slot_fill["home_foods"])
            plan.blocks = ["direct", "focus_compare", "tip", "followup"]
        elif slot == "safety":
            plan.blocks = ["ack", "safety_caution", "followup"] if state.risk_level() == "known" else ["ack", "followup"]
            plan.safety_caution = state.risk_level() == "known"
        elif slot == "food_preference":
            plan.blocks = ["ack", "followup"]
        elif slot == "sprouts_choice":
            plan.blocks = ["protocol_amount", "followup"]
        elif slot == "ingredients":
            plan.type = "meal_from_ingredients"
            plan.ingredients = list(u.slot_fill["ingredients"])
            plan.blocks = ["direct", "tips_list", "meal_place", "followup"]
            plan.protocol_level = "meal_place"
        _choose_followup(plan, state)
        return plan

    # ---- new questions ----
    _protocol_level(plan, u, state)
    is_weight = bool(set(u.conditions) & WEIGHT_CONDITIONS)
    if is_weight and not state.warm_opening_used and plan.type in {"food_choice_guidance", "protocol_explain", "support_struggle"}:
        plan.warm_opening = True
        plan.rules.append("R9")
    blocks: list[str] = ["opening"] if plan.warm_opening else []
    if u.is_reply and set(u.slot_fill) & {"safety", "food_preference"}:
        blocks.append("ack")

    if plan.type == "food_choice_guidance":
        plan.rules.append("R4")
        veg_mentioned = [f for f in u.foods if f not in u.pairs_with]
        if veg_mentioned and u.topic == "sabji":
            plan.focus_foods = veg_mentioned
            blocks += ["direct", "focus_compare", "tip"]
        else:
            blocks += ["direct"]
            if plan.protocol_level == "full":
                blocks += ["safety_caution", "protocol_full"]
            blocks += ["choices", "tip"]
    elif plan.type in {"food_allowed", "protocol_permission", "personal_suitability"}:
        plan.focus_foods = [f for f in u.foods if f not in u.pairs_with][:1] or u.foods[:1]
        blocks += ["food_position", "food_facts", "tip"]
        if plan.type == "personal_suitability":
            blocks.append("boundary")
        if not plan.focus_foods:
            blocks = [b for b in blocks if b != "food_position"] + ["unknown_food"]
    elif plan.type == "neutral_nutrient_lookup":
        plan.focus_foods = u.foods
        blocks += ["lookup_facts"]
    elif plan.type == "neutral_food_comparison":
        plan.focus_foods = u.foods[:3]
        blocks += ["direct", "focus_compare"]
    elif plan.type == "source_fact_lookup":
        blocks += ["sources"]
    elif plan.type in TIMING_TYPES:
        blocks += ["direct", "safety_caution", "protocol_full"]
        if plan.type == "protocol_explain":
            blocks.append("protocol_amounts")
        else:
            blocks.append("between_meals")
    elif plan.type == "status_dependent_protocol_question":
        blocks += ["status_unknown", "boundary"]
        plan.rules.append("R12")
    elif plan.type == "support_struggle":
        blocks += ["direct", "struggle", "boundary"]
    elif plan.type in RECIPE_TYPES:
        plan.rules.append("R7")
        plan.ingredients = list(u.ingredients or u.foods)
        blocks += ["direct", "tips_list", "meal_place"]

    # R2/R3: caution goes before the protocol whenever meal-timing advice is given and risk isn't "none".
    if plan.timing_advice and state.risk_level() != "none":
        plan.safety_caution = True
        plan.rules.append("R2" if state.risk_level() == "unknown" else "R3")
    else:
        blocks = [b for b in blocks if b != "safety_caution"]
    if plan.protocol_level == "reminder" and "protocol_full" in blocks:
        blocks[blocks.index("protocol_full")] = "protocol_reminder"
    blocks.append("followup")
    plan.blocks = blocks
    if plan.warm_opening or plan.protocol_level == "full" or plan.type in RECIPE_TYPES:
        plan.max_words = 180
    _choose_followup(plan, state)
    return plan
