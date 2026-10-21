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
    "FU-ALLERGY": ("allergies", "Do you have any food allergies, for example to milk, peanuts, nuts, gluten or eggs? "
                                "Say \"none\" if you don't."),
    "FU-CONFIRM": ("confirm_food", ""),          # text set when the recipe flow finds a close match
    "FU-RECIPE-CHOICE": ("recipe_choice", "Which one should I show?"),
    "FU-STATUS": ("diabetes_status", "Do you have diabetes or prediabetes, or have you had diabetes in the past?"),
    "FU-CONSULTANT": ("consultant", "Has your consultant confirmed that you no longer have diabetes?"),
}
REPLY_RECIPE_SLOTS = {"allergies", "confirm_food", "recipe_choice"}

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
    recipe_ids: list = field(default_factory=list)
    exclude_recipes: list = field(default_factory=list)
    more_request: bool = False
    wants_list: bool = False
    needs_status: bool = False            # the programme's answer depends on diabetes status (fruit)
    frequency_note: bool = False          # add the "no approved evidence on other eating patterns" line
    unknown_dish: str = ""
    rules: list = field(default_factory=list)
    max_words: int = 120
    emergency_category: str = ""          # E1-E7 when this turn is an emergency reply
    emergency_lines: dict = field(default_factory=dict)   # block -> fixed text (emergency, emergency_checkin, emergency_note)

    def as_dict(self) -> dict:
        return asdict(self)


def _choose_followup(plan: AnswerPlan, state: ChatState, allergies_known: bool = True, status=None) -> None:
    """R8: one question, from a fixed ladder; never a slot already filled or a question already asked."""
    asked = set(state.followups_asked)
    ladder: list[str] = []
    if plan.needs_status and status is not None:
        # R12 (NUT-STATUS-001): ask only when the rule really depends on it, once, and the consultant question after "now normal".
        if not status.known and state.status_asked_turn is None:
            ladder.append("FU-STATUS")
        elif status.value == "currently_non_diabetic" and status.consultant_validated is None \
                and state.consultant_asked_turn is None:
            ladder.append("FU-CONSULTANT")
    if (plan.type in RECIPE_TYPES or plan.wants_list) and not allergies_known and state.allergy_asked_turn is None:
        ladder.append("FU-ALLERGY")          # before the first food or recipe answer, ask once
    if plan.timing_advice and state.safety_asked_turn is None and state.risk_level() == "unknown":
        ladder.append("FU-SAFETY")
    if (plan.type in FOOD_TYPES or plan.reply_slot in {"safety", "home_foods"}) and not plan.needs_status:
        if not state.home_foods:
            ladder.append("FU-HOME-FOODS")
        if state.food_preference == "unknown":
            ladder.append("FU-PREFERENCE")
    if plan.type == "recipe_request" and not plan.ingredients and not state.ingredients_on_hand and not plan.recipe_ids \
            and not plan.wants_list:
        ladder.append("FU-INGREDIENTS")
    if plan.type in RECIPE_TYPES or plan.protocol_level in {"full", "meal_place"}:
        ladder.append("FU-SPROUTS")
    for fid in ladder:
        if fid in asked and fid not in {"FU-SAFETY", "FU-ALLERGY", "FU-STATUS", "FU-CONSULTANT"}:
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


def make_plan(u: Understanding, state: ChatState, emergency: bool = False, allergies_known: bool = True, status=None,
              em=None) -> AnswerPlan:
    plan = AnswerPlan(type=u.type, topic=u.topic, pairs_with=list(u.pairs_with))
    if emergency:
        plan.type, plan.blocks, plan.rules = "emergency", ["emergency"], ["R1"]
        if em is not None and em.is_emergency:
            plan.emergency_category = em.category
            plan.emergency_lines = {"emergency": em.reply}
        return plan
    if u.type == "greeting_thanks":
        plan.blocks = ["greeting"]
        return plan
    if u.type == "out_of_scope":
        plan.blocks = ["out_of_scope"]
        return plan
    if u.type == "explain_last":
        plan.blocks = ["explain_last"]
        return plan
    if u.type == "support_wellbeing":
        plan.blocks, plan.rules, plan.max_words = ["support"], ["R13"], 140
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
        elif slot in ("diabetes_status", "consultant"):
            last = state.last_question or {}
            plan.needs_status = True
            if last.get("type") in {"food_allowed", "protocol_permission", "personal_suitability"} and last.get("foods"):
                plan.type, plan.focus_foods = last["type"], list(last["foods"])
                plan.blocks = ["ack", "food_position", "food_facts", "followup"]
            elif last.get("type") == "status_dependent_protocol_question":
                plan.type = last["type"]
                plan.blocks = ["ack", "status_position", "boundary", "followup"]
            else:
                plan.blocks = ["ack", "followup"]
            plan.rules.append("R12")
        elif slot == "sprouts_choice":
            plan.blocks = ["protocol_amount", "followup"]
        elif slot == "ingredients" or slot in REPLY_RECIPE_SLOTS:
            # The answer to a recipe-flow question: run the recipe flow again with what we now know (allergies, the
            # confirmed food, the chosen dish).
            last = state.last_question or {}
            plan.type = last.get("type") if last.get("type") in RECIPE_TYPES else "meal_from_ingredients"
            plan.ingredients = list(u.slot_fill.get("ingredients") or state.ingredients_on_hand)
            if slot == "recipe_choice":
                plan.type, plan.recipe_ids = "recipe_request", [u.slot_fill["recipe_choice"]]
                plan.ingredients = []                 # a named dish: earlier pantry words don't apply
            elif last.get("recipe_ids"):
                plan.recipe_ids = list(last["recipe_ids"])
            plan.wants_list = bool(last.get("wants_list")) and not plan.recipe_ids and not plan.ingredients
            plan.blocks = ["recipe", "meal_place", "followup"]
            plan.protocol_level = "meal_place"
            plan.rules.append("R7")
            plan.max_words = 260
            if slot == "allergies" and u.ack_only:
                # The recipe already shown is unaffected by the allergies just given: don't repeat it.
                plan.blocks, plan.protocol_level, plan.max_words = ["ack", "followup"], "none", 120
        _choose_followup(plan, state, allergies_known, status)
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
            if u.conditions and not u.foods and plan.protocol_level != "full":
                # A person with a health condition asking what to eat: the general guidance, plus dishes that pass every check for them.
                # (Not on the answer that explains the whole programme: it ends with the medicines question, and the dishes come next turn.)
                plan.wants_list, plan.ingredients, plan.recipe_ids = True, [], []
                blocks += ["choices", "recipe"]
            else:
                blocks += ["choices", "tip"]
    elif plan.type in {"food_allowed", "protocol_permission", "personal_suitability"}:
        plan.focus_foods = [f for f in u.foods if f not in u.pairs_with][:1] or u.foods[:1]
        plan.needs_status = bool(set(plan.focus_foods) & set(u.fruit_foods))
        if plan.needs_status:
            plan.rules.append("R12")
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
        plan.frequency_note = u.frequency_question
    elif plan.type == "status_dependent_protocol_question":
        blocks += ["status_position", "boundary"]
        plan.needs_status = True
        plan.rules.append("R12")
    elif plan.type == "support_struggle":
        blocks += ["direct", "struggle", "boundary"]
    elif plan.type in RECIPE_TYPES:
        plan.rules.append("R7")
        plan.ingredients = list(u.ingredients or ([] if u.recipe_ids else u.foods))
        plan.recipe_ids = list(u.recipe_ids)
        plan.exclude_recipes = list(u.exclude_recipes)
        plan.more_request = u.more_request
        plan.unknown_dish = u.unknown_dish
        plan.wants_list = plan.type == "recipe_request" and not plan.recipe_ids and not plan.ingredients
        blocks += ["recipe", "meal_place"]

    # R2/R3: caution goes before the protocol whenever meal-timing advice is given and risk isn't "none".
    if plan.timing_advice and state.risk_level() != "none":
        plan.safety_caution = True
        plan.rules.append("R2" if state.risk_level() == "unknown" else "R3")
    else:
        blocks = [b for b in blocks if b != "safety_caution"]
    if plan.protocol_level == "reminder" and "protocol_full" in blocks:
        blocks[blocks.index("protocol_full")] = "protocol_reminder"
    if u.wellbeing:
        blocks.insert(0, "support_opening")       # a kind line and where to get help, before the food answer
    if plan.type in RECIPE_TYPES or plan.type in {"food_choice_guidance", "food_allowed", "protocol_permission", "personal_suitability"}:
        blocks.append("profile_note")        # says which allergies, conditions and diet the answer was checked against
    blocks.append("followup")
    plan.blocks = blocks
    if plan.type in RECIPE_TYPES:
        plan.max_words = 260
    elif plan.warm_opening or plan.protocol_level == "full":
        plan.max_words = 180
    _choose_followup(plan, state, allergies_known, status)
    return plan
