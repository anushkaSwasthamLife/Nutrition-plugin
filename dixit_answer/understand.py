"""Understand the message (stage 4): foods, ingredients, conditions, reply-to-our-question, question type.

Cue words in code, checked in a fixed order. The order and cue lists are drafts; the test set in tests/test_understand.py is
the reference. (A small AI tie-break can be added later; not needed for the cue sets here.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .foods import FoodIndex
from .textnorm import clean, has_phrase

TYPES = [
    "emergency", "followup_reply", "neutral_nutrient_lookup", "neutral_food_comparison", "source_fact_lookup",
    "food_allowed", "protocol_permission", "personal_suitability", "meal_protocol_application",
    "status_dependent_protocol_question", "food_choice_guidance", "meal_from_ingredients", "recipe_request",
    "protocol_explain", "support_struggle", "out_of_scope", "greeting_thanks",
]

GREETING = re.compile(r"^(hi|hello|hey|namaste|good (morning|evening|afternoon)|thanks?|thank you|thank you so much|ok|okay|"
                      r"ok thanks|okay thanks|thx|ty)( (dixit|coach|so much|a lot))?$")
INGREDIENTS = re.compile(r"\b(i have|i've got|i got|we have|at home (i|we) have|there is|there's|have only|only have)\b")
MAKE = re.compile(r"\b(make|prepare|cook|dish|meal|recipe|what can i (make|cook|prepare))\b")
RECIPE = re.compile(r"\b(recipe|how (do|to|can) (i |you )?(make|cook|prepare)|how is .* made|method to (make|cook))\b")
SOURCE = re.compile(r"\b(source|where (does|do|did) (this|that|these|the) .*(come|from)|where is (this|that) from|reference)\b")
LOOKUP = re.compile(r"\b(how (many|much)|what is the|what's the|amount of)\b.*\b(calorie|carb|protein|fat|fiber|sugar|energy|"
                    r"nutrition|nutrients?)\b|\b(calorie|carb|protein|fat|fiber)s? (in|of)\b")
COMPARE = re.compile(r"\b(compare|comparison|vs|versus|difference between|better than|which is better|healthier)\b")
STATUS = re.compile(r"\b(non diabetic|no longer diabetic|not diabetic anymore|reversed|reversal|now normal|sugar is normal)\b")
PERSONAL = re.compile(r"\b(safe for me|good for me|ok for me|okay for me|suitable for me|right for me|bad for me|harmful for me)\b")
MEAL_PROTOCOL = re.compile(r"\b(between meals|in between|snack|snacks|snacking|skip (breakfast|lunch|dinner|a meal)|two meals|"
                           r"2 meals|55 min|55 minutes|meal timing|when should i eat|how many meals|third meal|three meals)\b")
PERMISSION = re.compile(r"\b(allowed|permitted|in the program|in the protocol|as per the program|does the program allow)\b")
ALLOWED = re.compile(r"\b(can i (eat|have|take|drink)|should i (eat|have)|is it ok to (eat|have)|may i (eat|have))\b")
CHOICE = re.compile(r"\b(what (should|can|do) i (eat|have|cook)|what to eat|which (sabji|vegetable|food|foods|dal|fruit)|"
                    r"what (sabji|vegetable|food|foods)|what (kind of|type of) food|instead of|eat to (reduce|lose|control)|"
                    r"diet (for|to)|foods? (for|to) (reduce|lose|control)|what is good to eat|suggest (some )?(food|sabji|vegetable))\b")
EXPLAIN = re.compile(r"\b(how does (the |this |dixit )?(program|diet|plan|lifestyle) work|what is (the )?dixit|explain (the )?"
                     r"(program|rules|diet|plan)|what are the rules|tell me about (the )?(program|dixit))\b")
STRUGGLE = re.compile(r"\b(hungry|hunger|craving|cravings|not losing|no weight loss|weight (is )?not (reducing|going down)|"
                      r"plateau|stuck|tired|weak|weakness|dizzy|headache|difficult to follow|can't follow|cannot follow)\b")
DIET_WORDS = re.compile(r"\b(eat|eating|food|foods|meal|meals|diet|weight|sabji|vegetable|cook|cooking|drink|nutrition|program|"
                        r"dixit|sugar|calorie|carb|protein|hungry|recipe|breakfast|lunch|dinner|fast|fasting)\b")
TOPIC_SABJI = re.compile(r"\b(sabji|vegetable|curry)\b")


@dataclass
class Understanding:
    cleaned: str
    type: str
    foods: list = field(default_factory=list)            # food master IDs mentioned
    ingredients: list = field(default_factory=list)      # food IDs the person has on hand
    conditions: list = field(default_factory=list)
    topic: str = "home_meal"                             # "sabji" or "home_meal"
    pairs_with: list = field(default_factory=list)       # e.g. chapati in "what sabji with chapati"
    slot_fill: dict = field(default_factory=dict)        # reply to our last question
    is_reply: bool = False
    unknown_food_query: str = ""                         # food-like words not in the food master
    preference: str = ""


def _conditions(cleaned: str, word_lists: dict) -> list[str]:
    return [name for name, terms in word_lists["conditions"].items() if any(has_phrase(cleaned, clean(t)) for t in terms)]


def _preference(cleaned: str, word_lists: dict) -> str:
    # Most specific first: "non veg" also contains "veg".
    for name in ("non_vegetarian", "egg", "jain", "vegetarian"):
        if any(has_phrase(cleaned, clean(t)) for t in word_lists["preference_words"][name]):
            return name
    return ""


def classify(cleaned: str, foods: list[str], index: FoodIndex) -> str:
    if GREETING.match(cleaned):
        return "greeting_thanks"
    if INGREDIENTS.search(cleaned) and MAKE.search(cleaned):
        return "meal_from_ingredients"
    if RECIPE.search(cleaned):
        return "recipe_request"
    if SOURCE.search(cleaned):
        return "source_fact_lookup"
    if LOOKUP.search(cleaned):
        return "neutral_nutrient_lookup"
    if COMPARE.search(cleaned) and len(foods) >= 2:
        return "neutral_food_comparison"
    if STATUS.search(cleaned):
        return "status_dependent_protocol_question"
    if PERSONAL.search(cleaned):
        return "personal_suitability"
    if EXPLAIN.search(cleaned):
        return "protocol_explain"
    if STRUGGLE.search(cleaned) and not ALLOWED.search(cleaned):
        return "support_struggle"        # "I feel hungry between meals" is a struggle, not a timing question
    if MEAL_PROTOCOL.search(cleaned):
        return "meal_protocol_application"
    if CHOICE.search(cleaned):
        return "food_choice_guidance"
    if PERMISSION.search(cleaned) and foods:
        return "protocol_permission"
    if ALLOWED.search(cleaned) and foods:
        return "food_allowed"
    if foods and len(cleaned.split()) <= 4:
        return "food_allowed"            # a bare food name: say what the programme says about it, with facts
    if DIET_WORDS.search(cleaned) or foods:
        return "food_choice_guidance"
    return "out_of_scope"


def read_reply(cleaned: str, slot: str, foods: list[str], index: FoodIndex, word_lists: dict) -> dict:
    """Try to read the message as an answer to our last question. Returns the slot values, or {}."""
    if slot == "home_foods":
        veg = [f for f in foods if index.foods[f]["category"] in {"vegetable", "starchy_vegetable"}]
        return {"home_foods": veg} if veg else {}
    if slot == "food_preference":
        pref = _preference(cleaned, word_lists)
        return {"food_preference": pref} if pref else {}
    if slot == "sprouts_choice":
        if "FOOD-EGG" in foods:
            return {"sprouts_choice": "eggs"}
        if "FOOD-SPROUTS" in foods:
            return {"sprouts_choice": "sprouts"}
        return {}
    if slot == "ingredients":
        return {"ingredients": foods} if foods else {}
    if slot == "safety":
        words = set(cleaned.split())
        markers = {clean(w) for w in word_lists["no_words"] + word_lists["yes_words"]}
        risk_terms = [clean(t) for terms in word_lists["risk_terms"].values() for t in terms]
        if words & {m for m in markers if " " not in m} or any(has_phrase(cleaned, m) for m in markers if " " in m) \
                or any(has_phrase(cleaned, t) for t in risk_terms):
            return {"safety": True}
        return {}
    return {}


def understand(text: str, state, index: FoodIndex, word_lists: dict) -> Understanding:
    cleaned = clean(text, word_lists["spelling_map"])
    mentions = index.find(cleaned, already_clean=True)
    foods = [m.food_id for m in mentions]
    u = Understanding(cleaned=cleaned, type="", foods=foods, conditions=_conditions(cleaned, word_lists),
                      preference=_preference(cleaned, word_lists))
    if TOPIC_SABJI.search(cleaned):
        u.topic = "sabji"
    staples = [f for f in foods if index.foods[f]["category"] == "staple"]
    if u.topic == "sabji" and staples:
        u.pairs_with = staples

    # Reply to our last question (only the question asked in the previous turn counts).
    last = state.last_followup
    if last and last.get("turn") == state.turn:
        u.slot_fill = read_reply(cleaned, last["slot"], foods, index, word_lists)

    question_type = classify(cleaned, foods, index)
    if INGREDIENTS.search(cleaned) and question_type == "meal_from_ingredients":
        u.ingredients = foods
    if u.slot_fill:
        # A short answer with no question of its own is a pure reply (R6). A longer message can carry a new question too:
        # "No medicines. I have chapati, what sabji can I eat?"
        has_own_question = question_type not in {"food_allowed", "food_choice_guidance", "out_of_scope", "greeting_thanks"} \
            or "?" in text or CHOICE.search(cleaned) or ALLOWED.search(cleaned)
        if len(cleaned.split()) <= 6 and not has_own_question:
            u.type, u.is_reply = "followup_reply", True
            return u
        u.is_reply = True
    u.type = question_type
    return u
