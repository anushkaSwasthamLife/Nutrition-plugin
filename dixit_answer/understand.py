"""Understand the message (stage 4): foods, ingredients, conditions, reply-to-our-question, question type.

Cue words in code, checked in a fixed order. The order and cue lists are drafts; the test set in tests/test_understand.py is
the reference. (A small AI tie-break can be added later; not needed for the cue sets here.)
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .foods import FoodIndex
from .targets import WANT, Target, is_target_request, parse_targets
from .semantics import NON_FOOD_WORDS, NEGATED, NEGATED_AFTER, meaningful_words, negated_foods, split_chunks
from .status import parse_status
from .textnorm import clean, has_phrase

TYPES = [
    "emergency", "followup_reply", "neutral_nutrient_lookup", "neutral_food_comparison", "source_fact_lookup",
    "food_allowed", "protocol_permission", "personal_suitability", "meal_protocol_application",
    "status_dependent_protocol_question", "food_choice_guidance", "meal_from_ingredients", "recipe_request",
    "protocol_explain", "support_struggle", "out_of_scope", "greeting_thanks",
]

GREETING = re.compile(r"^(hi|hello|hey|namaste|good (morning|evening|afternoon)|thanks?|thank you|thank you so much|ok|okay|"
                      r"ok thanks|okay thanks|thx|ty)( (dixit|coach|so much|a lot))?$")
INGREDIENTS = re.compile(r"\b(i have|i've got|i got|we have|at home (i|we) have|there is|there's|have only|only have|my paas)\b")
HINDI_MAKE = re.compile(r"\b(bana|banau|banaun|banaye|pakau|pakaun|pakana|pakaye|kya bana)\b")   # not "banana": that's a fruit
DISH_AFTER = re.compile(r"(?:recipe (?:for|of)|how (?:do|to|can|should) (?:i |you |we )?(?:make|cook|prepare)|make|cook|prepare)\s+(.+)$")
DISH_FILLER = {"please", "pls", "show", "me", "the", "a", "an", "recipe", "recipes", "for", "i", "want", "give", "ka", "ki", "ke", "ko", "dikhao", "batao",
               "chahiye", "of", "to", "and", "ok", "okay", "yes", "this", "that", "one", "sure", "make", "how"}
GENERIC_DISH = {"recipes", "recipe", "some recipes", "a recipe", "something", "food", "a dish", "dish", "dishes", "some dishes",
                "me recipes", "it", "this", "that", "meal", "meals", "lunch", "dinner"}
MAKE = re.compile(r"\b(make|prepare|cook|dish|meal|recipe|what can i (make|cook|prepare))\b")
RECIPE = re.compile(r"\b(recipe|how (do|to|can) (i |you )?(make|cook|prepare)|how is .* made|method to (make|cook))\b")
SOURCE = re.compile(r"\b(source|where (does|do|did) (this|that|these|the) .*(come|from)|where is (this|that) from|reference)\b")
LOOKUP = re.compile(r"\b(how (many|much)|what is the|what's the|amount of)\b.*\b(calorie|carb|protein|fat|fiber|sugar|energy|"
                    r"nutrition|nutrients?)\b|\b(calorie|carb|protein|fat|fiber)s? (in|of)\b")
COMPARE = re.compile(r"\b(compare|comparison|vs|versus|difference between|better than|which is better|healthier)\b")
STATUS = re.compile(r"\b(non diabetic|no longer diabetic|not diabetic anymore|reversed|reversal|now normal|sugar is normal)\b")
PERSONAL = re.compile(r"\b(safe for me|good for me|ok for me|okay for me|suitable for me|right for me|bad for me|harmful for me)\b")
MEAL_PROTOCOL = re.compile(r"\b(between meals|in between|snack|snacks|snacking|skip (breakfast|lunch|dinner|a meal)|two meals|"
                           r"2 meals|55 min|55 minutes|meal timing|when should i eat|how many meals|third meal|three meals|"
                           r"every (two|three|2|3|4|four|few) hours|small meals|frequent meals|small frequent|five meals|six meals|"
                           r"eat more often|eating more often|more meals)\b")
FREQUENCY = re.compile(r"\b(every (two|three|2|3|4|four|few) hours|small meals|frequent meals|small frequent|five meals|six meals|"
                       r"eat more often|eating more often|more meals|three meals|how many meals)\b")
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
# A person saying how they feel (anxious, stressed, low): answered with care and approved wording, never with food claims.
WELLBEING = re.compile(r"\b(anxious|anxiety|panic|panicking|stressed|stress|stressful|nervous|overwhelmed|worried|worrying|tension|"
                       r"depressed|depression|hopeless|feeling low|feel low|feel sad|feeling sad|am sad|so sad|very sad|low mood|"
                       r"ghabrahat|ghabra|ghabrata|ghabrati|chinta|pareshan|tanav)\b")
NUTRIENT_WORDS = re.compile(r"\b(calorie|calories|kcal|carb|carbs|carbohydrate|carbohydrates|protein|proteins|fat|fats|fibre|fiber|"
                            r"nutrition|nutrients?)\b")
# "paneer, other than paneer bhurji": what comes after is a dish to leave out, not something the person has or a food we don't know
EXCLUDE = re.compile(r"\b(?:other than|apart from|aside from|except for|except|besides|excluding|not counting|but not|anything but)\s+(?P<x>.+)$")
# "what else", "any other", "something different": a follow-up that only makes sense after the last answer
MORE = re.compile(r"\b(what else|anything else|something else|any other|any more|more options|other options|another one|another|one more|"
                  r"something different|show more|different one|other ones?|else|more)\b")
# "why?", "tell me more", "where is that from": only the written reason or source of the last answer is given, never a new claim
EXPLAIN_FOLLOW = re.compile(r"^(?:ok |okay |but |and |so )?(?:why|why so|why is that|why is it|why is that so|how come|tell me more|more details|more info|"
                            r"more information|explain|explain that|explain it|explain more|explain please|what do you mean|how so|where is that from|"
                            r"where is this from|where does that come from|where does this come from|source|sources|how do you know|reason|the reason|"
                            r"what is the reason|why please|why is this)$")
RECIPE_FLOW = {"recipe_request", "meal_from_ingredients"}
TOPIC_SABJI = re.compile(r"\b(sabji|vegetable|curry)\b")


@dataclass
class Understanding:
    cleaned: str
    type: str
    focus: str = ""                                      # the message without the sentences that only state an allergy
    foods: list = field(default_factory=list)            # food master IDs mentioned
    ingredients: list = field(default_factory=list)      # food IDs the person has on hand
    wellbeing: bool = False                              # the message says how the person feels (anxious, stressed, low)
    conditions: list = field(default_factory=list)
    topic: str = "home_meal"                             # "sabji" or "home_meal"
    pairs_with: list = field(default_factory=list)       # e.g. chapati in "what sabji with chapati"
    slot_fill: dict = field(default_factory=dict)        # reply to our last question
    is_reply: bool = False
    unknown_food_query: str = ""                         # food-like words not in the food master
    preference: str = ""
    recipe_ids: list = field(default_factory=list)       # approved recipes named in the message
    target_exclude: list = field(default_factory=list)   # words to leave out of a dish search ("paneer")
    target_mode: str = "new"                             # new | refine (changed amounts or something left out) | more (what else)
    targets: list = field(default_factory=list)          # nutrient amounts asked for ("326 kcal", "26 g protein"): a dish search
    subject: str = ""                                    # the food or dish this question is about (for "and protein?" next)
    subject_kind: str = ""
    subject_nutrient: str = ""
    more_request: bool = False                           # "what else": more of the last recipe answer, leaving out what was shown
    absent_foods: list = field(default_factory=list)     # foods the person said they do NOT have ("no onion")
    exclude_foods: list = field(default_factory=list)    # foods the person wants no recipe to use ("without onion" after a recipe answer)
    exclude_recipes: list = field(default_factory=list)  # approved recipes the person asked to leave out ("other than paneer bhurji")
    unknown_words: list = field(default_factory=list)    # pantry words we don't know (never sent to the AI)
    suggestion: dict | None = None                       # {"word", "food_id"} close match to confirm first
    ack_only: bool = False                               # allergy reply that doesn't affect the recipe already shown
    unknown_dish: str = ""                               # a dish asked for by name that isn't in the recipe collection
    fruit_foods: list = field(default_factory=list)      # fruit mentioned (the Golden Rule for fruit depends on diabetes status)
    frequency_question: bool = False                     # "is eating every three hours better?": the programme's position, no evidence


def _conditions(cleaned: str, word_lists: dict) -> list[str]:
    return [name for name, terms in word_lists["conditions"].items() if any(has_phrase(cleaned, clean(t)) for t in terms)]


def scrub_conditions(cleaned: str, word_lists: dict) -> str:
    """The message with every health-condition phrase taken out, so "I have obesity" is not read as a list of foods."""
    terms = sorted({clean(t) for terms in word_lists["conditions"].values() for t in terms}, key=len, reverse=True)
    for term in terms:
        if term:
            cleaned = re.sub(r"(?<![\w])" + re.escape(term) + r"(?![\w])", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _preference(cleaned: str, word_lists: dict) -> str:
    # Most specific first: "non veg" also contains "veg".
    for name in ("non_vegetarian", "egg", "jain", "vegetarian"):
        if any(has_phrase(cleaned, clean(t)) for t in word_lists["preference_words"][name]):
            return name
    return ""


def classify(cleaned: str, foods: list[str], index: FoodIndex) -> str:
    if GREETING.match(cleaned):
        return "greeting_thanks"
    if WELLBEING.search(cleaned) and not RECIPE.search(cleaned) and not LOOKUP.search(cleaned) and not TOPIC_SABJI.search(cleaned) and not any(index.foods[f]["category"] not in {"staple", "pantry", "sweet"} for f in foods) and not (INGREDIENTS.search(cleaned) and (MAKE.search(cleaned) or HINDI_MAKE.search(cleaned))):
        return "support_wellbeing"        # how someone feels comes first; a recipe request in the same message is answered with a kind line in front
    if INGREDIENTS.search(cleaned) and (MAKE.search(cleaned) or HINDI_MAKE.search(cleaned)):
        return "meal_from_ingredients"
    if HINDI_MAKE.search(cleaned) and any(index.foods[f]["category"] not in {"staple", "pantry", "sweet"} for f in foods):
        return "meal_from_ingredients"       # "aloo pyaz hai kya banau": foods plus a Hindi "what can I make"
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
    cookable = [f for f in foods if index.foods[f]["category"] not in {"staple", "pantry", "sweet"}]
    if INGREDIENTS.search(cleaned) and cookable and not STRUGGLE.search(cleaned):
        return "meal_from_ingredients"   # "I have cauliflower and potato": a list of what's at home is a recipe request
    if foods and len(cleaned.split()) <= 4:
        return "food_allowed"            # a bare food name: say what the programme says about it, with facts
    if any(index.foods[f]["category"] == "fruit" and f != "FOOD-LEMON" for f in foods):
        return "food_allowed"            # a question about a fruit: the programme's fruit position, plus facts
    if DIET_WORDS.search(cleaned) or foods:
        return "food_choice_guidance"
    return "out_of_scope"


CLAUSE_END = re.compile(r"\b(so|what|how|which|can|could|please|tell|give|suggest|make|cook|prepare|any|for|to|is|are|"
                        r"kya|bana|banau|banaun|sakta|sakti)\b")


# ---- follow-ups that only make sense after the last answer: "and protein?", "what about dal?", "how many calories in it" ----
NUTRIENT_CANON = [(re.compile(r"\b(calorie|calories|kcal|energy)s?\b"), "calories"), (re.compile(r"\b(carb|carbs|carbohydrates?)s?\b"), "carbs"),
                  (re.compile(r"\bproteins?\b"), "protein"), (re.compile(r"\bfats?\b"), "fat"), (re.compile(r"\bfib(re|er)s?\b"), "fibre")]
PRONOUNS = {"it", "this", "that", "them", "these", "those"}
ASK_FILLER = {"and", "also", "what", "whats", "what's", "about", "how", "ok", "okay", "its", "it's", "their", "the", "of", "it", "in", "for", "many", "much",
              "is", "are", "there", "tell", "me", "give", "show", "kitna", "kitni", "hai", "a", "an", "does", "do", "has", "have", "per", "serving", "amount"}
FOLLOW_LEAD = re.compile(r"^(?:and what about|and how about|what about|how about|what of|ok and|okay and|and)\s+(?P<x>.+)$")
RECENT_TURNS = 3


def canonical_nutrient(cleaned: str) -> str:
    for pattern, name in NUTRIENT_CANON:
        if pattern.search(cleaned.lower()):
            return name
    return ""


def carry_over(text: str, state, index, word_lists: dict, dish_finder=None) -> str:
    """Rewrite a short follow-up into a full question using what the last answer was about; anything else comes back unchanged.

    "and protein?" after calories in poha -> "protein in poha"; "what about dal?" -> "protein in dal" (the same nutrient) or "can i eat dal"
    (after a can-I-eat question); "is it good for diabetes" -> "is poha good for diabetes". Only within a few turns of the last
    question and only for short messages that name no food of their own."""
    subject = getattr(state, "last_subject", "")
    if not subject or state.turn - getattr(state, "last_subject_turn", -99) > RECENT_TURNS:
        return text
    cleaned = clean(question_part(text), word_lists["spelling_map"])
    words = cleaned.split()
    if not words or len(words) > 8:
        return text
    nutrient = canonical_nutrient(cleaned)
    own_foods = index.find(cleaned, already_clean=True)
    own_dishes = dish_finder(cleaned) if dish_finder else []
    kind = getattr(state, "last_subject_kind", "")
    if not own_foods and not own_dishes:
        leftover = [w for w in words if w not in ASK_FILLER and w not in PRONOUNS and not NUTRIENT_WORD_RE.fullmatch(w)]
        if nutrient and not leftover:
            return f"{nutrient} in {subject}"                                       # "and protein?"
        if PRONOUNS & set(words):
            if nutrient:
                return " ".join(subject if w in PRONOUNS else w for w in words)          # "how many calories in it"
            if kind in {"nutrient", "allowed"} and not NUTRIENT_WORDS.search(cleaned) and len(words) <= 7 and re.search(
                    r"\b(good|ok|okay|safe|allowed|bad|fine|healthy|suit|suitable|eat|have|take|diabetes|diabetic|weight|sugar|bp)", cleaned):
                return " ".join(subject if w in PRONOUNS else w for w in words)          # "is it good for diabetes"
        return text
    lead = FOLLOW_LEAD.match(cleaned)
    if lead and not nutrient:
        x = lead.group("x")
        if kind == "nutrient" and getattr(state, "last_nutrient", ""):
            return f"{state.last_nutrient} in {x}"                                    # "what about dal?" after a protein question
        if kind == "allowed":
            return f"can i eat {x}"
    return text


NUTRIENT_WORD_RE = re.compile(r"(?:calorie|calories|kcal|energy|carb|carbs|carbohydrate|carbohydrates|protein|proteins|fat|fats|fibre|fiber|nutrition|nutrients?)")


# ---- a follow-up to a dish search: "except paneer", "without onion", "make it 30 g protein", "what else" ----
TARGET_EXCLUDE = re.compile(r"^(?:(?:but|and|also|ok|okay|please|now|then)\s+)*(?:except(?: for)?|excluding|without|no|not|other than|apart from|besides|skip|"
                            r"avoid|leave out|remove|minus)\s+(?P<x>.+?)(?:\s+(?:please|pls))?$")
EXCLUDE_FILLER = {"the", "any", "anything", "and", "or", "of", "a", "an", "all", "dishes", "dish", "with", "in", "it", "that", "those", "food", "foods", "thanks", "thank", "you", "need", "more", "ok", "okay", "yes", "now", "today", "worries", "problem"}


def target_refinement(text: str, state, index: FoodIndex, word_lists: dict):
    """A short follow-up to the last dish search, read as the same search with a change; None when it is not one."""
    last = getattr(state, "last_targets", None)
    if not last or state.turn - getattr(state, "last_targets_turn", -99) > RECENT_TURNS:
        return None
    cleaned = clean(question_part(text), word_lists["spelling_map"])
    words = cleaned.split()
    if not words or len(words) > 10:
        return None
    targets = [Target(*t) for t in last]
    exclude = list(getattr(state, "target_exclude", []) or [])
    mode = None
    cut = TARGET_EXCLUDE.match(cleaned)
    new = parse_targets(cleaned)
    if cut:
        tokens = [w for w in cut.group("x").split() if w not in EXCLUDE_FILLER]
        if tokens:
            names = set(tokens)
            for mention in index.find(" ".join(tokens), already_clean=True):      # "curd" also leaves out dahi, "potato" also aloo
                food = index.foods[mention.food_id]
                names |= {a for a in [food["name"], *food.get("aliases", [])] if " " not in a}
            exclude += sorted(n for n in names if n not in exclude)
            mode = "refine"
    elif new and not WANT.search(cleaned):          # a full new request ("i want a meal with ...") starts a new search
        merged = {t.field: t for t in targets}
        merged.update({t.field: t for t in new})
        targets, mode = list(merged.values()), "refine"
    elif MORE.search(cleaned) and len(words) <= 5 and not index.find(cleaned, already_clean=True):
        mode = "more"
    if mode is None:
        return None
    u = Understanding(cleaned=cleaned, type="target_meal_search", focus=cleaned)
    u.targets, u.target_exclude, u.target_mode = targets, exclude, mode
    return u


def pantry_words(cleaned: str, index: FoodIndex, stop_words: list[str], judge=None) -> tuple[list[str], dict | None, set]:
    """R1: words after "I have ..." that aren't known foods. Returns (unknown words, close-match suggestion or None, foods said to be absent).

    Wishes, tastes, times and people ("quick", "for dinner", "my kids") are not foods; "no onion" / "without onion" / "onion nahi hai" say
    the food is NOT on hand. What is left is checked against the food list, then (optionally) by the meaning check in semantics.py."""
    m = INGREDIENTS.search(cleaned)
    if not m:
        return [], None, set()
    clause = cleaned[m.end():]
    end = CLAUSE_END.search(clause)
    clause = clause[:end.start()] if end else clause
    clause = re.split(r"\b(?:i am|am|i m|im|allergic|allergy|allergies)\b", clause)[0]     # a statement about the person is not a food
    absent = negated_foods(clause, index)
    kept = []
    for chunk in split_chunks(clause):
        if not (NEGATED.match(chunk) or NEGATED_AFTER.search(chunk)):       # a negated statement is about what is missing, not a pantry word
            kept.append(chunk)
    clause = " , ".join(kept)
    found = {m.food_id for m in index.find(clause, already_clean=True)}
    aliases = {m.alias for m in index.find(clause, already_clean=True)} | {a for a, fid in index.alias_to_id.items() if fid in found}
    for alias in sorted(aliases, key=len, reverse=True):          # every spelling of a known food, not just the first one found
        clause = re.sub(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", " , ", clause)
    unknown = []
    for chunk in re.split(r",|\band\b|\bor\b|&", clause):
        phrase = " ".join(meaningful_words(chunk, stop_words))
        if phrase and re.fullmatch(r"[a-z' ]{2,30}", phrase) and phrase not in unknown:   # short plain words only, never echoed raw
            unknown.append(phrase)
    unknown = unknown[:5]
    suggestion = None
    for word in unknown:
        close = difflib.get_close_matches(word, list(index.alias_to_id), n=1, cutoff=0.75)
        if close:
            suggestion = {"word": word, "food_id": index.alias_to_id[close[0]]}
            break
    if judge is not None:
        for word in list(unknown)[:3]:
            if suggestion and suggestion["word"] == word:
                continue
            verdict = judge(word)
            if verdict is None:
                continue
            if verdict[0] == "not_food":
                unknown.remove(word)                      # a wish or a describing word, not something to look up
            elif verdict[0] == "same" and not suggestion:
                suggestion = {"word": word, "food_id": verdict[1]}      # confirmed with the person first, as for a close spelling
    return unknown, suggestion, absent


def read_reply(cleaned: str, slot: str, foods: list[str], index: FoodIndex, word_lists: dict,
               allergy_reader=None, recipe_ids=None) -> dict:
    """Try to read the message as an answer to our last question. Returns the slot values, or {}."""
    if slot == "diabetes_status":
        found = parse_status(cleaned, reply=True)
        if not found and set(cleaned.split()) & {"no", "nope", "nahi", "never", "none"}:
            found = {"status": "non_diabetic", "prior_history": False}
        return {"diabetes_status": found} if found else {}
    if slot == "consultant":
        found = parse_status(cleaned, reply=True)
        if "consultant_validated" in found:
            return {"consultant": found["consultant_validated"]}
        words = set(cleaned.split())
        if words & {"yes", "yeah", "yep", "haan", "ha", "confirmed", "validated"}:
            return {"consultant": True}
        if words & {"no", "nope", "nahi", "not", "never"}:
            return {"consultant": False}
        return {}
    if slot == "allergies" and allergy_reader:
        allergies = allergy_reader(cleaned)
        return {"allergies": allergies} if allergies is not None else {}
    if slot == "recipe_choice":
        return {"recipe_choice": recipe_ids[0]} if recipe_ids else {}
    if slot == "confirm_food":
        words = set(cleaned.split())
        if words & {"yes", "yeah", "yep", "haan", "ha", "right", "correct", "yup"}:
            return {"confirm_food": True}
        if words & {"no", "nope", "nahi", "not"}:
            return {"confirm_food": False}
        return {"confirm_food": foods[0]} if foods else {}
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


ALLERGY_SENTENCE = re.compile(r"\b(allergic|allergy|allergies)\b", re.IGNORECASE)


def question_part(text: str) -> str:
    """The message without sentences that only state an allergy ("I am allergic to peanuts. calories in poha" -> "calories in poha"),
    so a food named as an allergy is not read as the food being asked about. A message that is only an allergy is left whole."""
    sentences = [s for s in re.split(r"(?<=[.!?;])\s+|\n+", text or "") if s.strip()]
    kept = [s for s in sentences if not ALLERGY_SENTENCE.search(s)]
    return " ".join(kept) if kept else (text or "")


ABOUT = re.compile(r"^(?:what is|what's|what s|whats|what are|what about|tell me about|tell me more about|about|info on|information on|show me|nutrition of|nutrition for)\s+")
BARE_FILLER = {"a", "an", "the", "some", "one", "and", "aur", "or", "please", "pls", "ok", "okay"}
BARE_TAIL = re.compile(r"\s+(?:nutrition|info|information|details)$")


def _bare_dish(focus: str, dish_finder) -> bool:
    """True when the whole message is one or more dish names ("poha", "what is masala dosa", "upma and poha") and nothing else."""
    words = BARE_TAIL.sub("", ABOUT.sub("", focus.strip(), count=1)).strip()
    if not words or len(words) > 80:
        return False
    hits = dish_finder(words)
    if not hits:
        return False
    rest = f" {words} "
    for hit in hits:
        rest = rest.replace(f" {hit['span']} ", " ", 1)
    return all(w in BARE_FILLER for w in rest.split())


def understand(text: str, state, index: FoodIndex, word_lists: dict, recipe_finder=None, allergy_reader=None,
               dish_finder=None, word_judge=None) -> Understanding:
    again = target_refinement(text, state, index, word_lists)
    if again is not None:
        return again
    text = carry_over(text, state, index, word_lists, dish_finder)
    cleaned = clean(text, word_lists["spelling_map"])
    focus = clean(question_part(text), word_lists["spelling_map"])
    excluded_dishes: list = []
    whole = cleaned
    cut = EXCLUDE.search(cleaned)
    if cut:
        if recipe_finder:
            excluded_dishes = recipe_finder(cut.group("x"))
        cleaned = cleaned[:cut.start()].strip()                 # what is left is what the person has and asks
        focus_cut = EXCLUDE.search(focus)
        focus = focus[:focus_cut.start()].strip() if focus_cut else focus
    mentions = index.find(scrub_conditions(focus, word_lists), already_clean=True)         # "high sugar" is a condition, not the food sugar
    foods = [m.food_id for m in mentions]
    u = Understanding(cleaned=cleaned, type="", focus=focus, foods=foods, conditions=_conditions(cleaned, word_lists),
                      preference=_preference(cleaned, word_lists))
    u.exclude_recipes = list(excluded_dishes)
    u.wellbeing = bool(WELLBEING.search(cleaned))
    u.fruit_foods = [f for f in foods if index.foods[f]["category"] == "fruit" and f != "FOOD-LEMON"]
    if recipe_finder:
        u.recipe_ids = recipe_finder(cleaned)
    if TOPIC_SABJI.search(cleaned):
        u.topic = "sabji"
    staples = [f for f in foods if index.foods[f]["category"] == "staple"]
    if u.topic == "sabji" and staples:
        u.pairs_with = staples

    # Reply to our last question (only the question asked in the previous turn counts).
    last = state.last_followup
    if last and last.get("turn") == state.turn:
        u.slot_fill = read_reply(cleaned, last["slot"], foods, index, word_lists, allergy_reader, u.recipe_ids)

    question_type = classify(cleaned, foods, index)
    u.frequency_question = bool(FREQUENCY.search(cleaned)) and question_type == "meal_protocol_application"
    if question_type == "out_of_scope" and not u.slot_fill and parse_status(cleaned):
        question_type = "status_dependent_protocol_question"       # "yes, my consultant confirmed it": a status statement
    if u.recipe_ids and question_type in {"food_allowed", "food_choice_guidance", "meal_from_ingredients"} and "?" not in text:
        # A dish name on its own ("Moong Dal Chilla", "show me jeera aloo please") is a request for that recipe.
        leftover = set(getattr(recipe_finder, "__self__", None).last_leftover) if hasattr(recipe_finder, "__self__") else set()
        if leftover <= DISH_FILLER:
            question_type = "recipe_request"
    if question_type != "recipe_request" and not (u.slot_fill and "recipe_choice" in u.slot_fill):
        u.recipe_ids = []                # a dish name only counts when the person is asking for that recipe
    if question_type == "recipe_request" and not u.recipe_ids:
        m = DISH_AFTER.search(cleaned)
        if m:
            dish = " ".join(w for w in m.group(1).split() if w not in {"please", "pls", "some", "a", "an", "the", "me", "for", "my"})
            if dish and dish not in GENERIC_DISH and not u.foods and any(w not in NON_FOOD_WORDS for w in dish.split()):
                u.unknown_dish = dish
    if INGREDIENTS.search(cleaned) and question_type == "meal_from_ingredients":
        u.ingredients = foods
        u.unknown_words, u.suggestion, absent = pantry_words(scrub_conditions(cleaned, word_lists), index,
                                                             word_lists.get("ingredient_stop_words", []), word_judge)
        if absent:                                          # "paneer but no onion": onion is not in the kitchen
            u.absent_foods = sorted(absent)
            u.ingredients = [f for f in u.ingredients if f not in absent]
            u.foods = [f for f in u.foods if f not in absent]
        if u.conditions and not u.ingredients and not u.unknown_words and not u.recipe_ids:
            question_type = "recipe_request"        # "I have obesity, what sabji would you recommend": no foods at home were named
            u.suggestion = None
    if u.slot_fill:
        # A short answer with no question of its own is a pure reply (R6). A longer message can carry a new question too:
        # "No medicines. I have chapati, what sabji can I eat?"
        has_own_question = question_type not in {"food_allowed", "food_choice_guidance", "out_of_scope", "greeting_thanks",
                                                 "recipe_request"} \
            or "?" in text or CHOICE.search(cleaned) or ALLOWED.search(cleaned)
        if len(cleaned.split()) <= 6 and not has_own_question:
            u.type, u.is_reply = "followup_reply", True
            return u
        u.is_reply = True
    if question_type == "out_of_scope" and u.conditions and not u.slot_fill:
        question_type = "food_choice_guidance"       # "I have obesity": a health condition on its own is a food question in this programme
    if dish_finder and not u.slot_fill and NUTRIENT_WORDS.search(cleaned):
        hits = dish_finder(focus)
        if hits and not foods and question_type in {"out_of_scope", "food_choice_guidance", "food_allowed"}:
            question_type = "neutral_nutrient_lookup"       # "poha calories": a dish from the INDB data, asked in a few words
        elif hits and foods and question_type == "neutral_nutrient_lookup":
            inside = {m.food_id for m in mentions for h in hits if has_phrase(h["span"], m.alias) and h["span"] != m.alias}
            u.foods = [f for f in u.foods if f not in inside]      # "calories in chilli paneer" is the dish, not the food paneer
    elif dish_finder and not u.slot_fill and not foods and question_type in {"out_of_scope", "food_choice_guidance"} and _bare_dish(focus, dish_finder):
        question_type = "neutral_nutrient_lookup"           # "poha", "what is poha?": the whole message is a dish name, so give its calories
    if question_type not in {"support_wellbeing", "greeting_thanks", "followup_reply"} and not u.slot_fill and not foods and not u.recipe_ids:
        targets = is_target_request(cleaned)
        if targets and not (dish_finder and dish_finder(focus)):
            question_type, u.targets = "target_meal_search", targets      # "a meal with 326 kcal and 26 g protein": dishes near those values
            u.type = question_type
            return u
    if ((question_type in {"out_of_scope", "food_choice_guidance"} or (question_type == "source_fact_lookup" and (getattr(state, "last_explain", None) or {}).get("kind") == "recipe"))
            and not u.slot_fill and EXPLAIN_FOLLOW.match(cleaned)
            and getattr(state, "last_explain", None) and state.turn - getattr(state, "last_explain_turn", -99) <= RECENT_TURNS):
        u.type = "explain_last"
        return u
    last = getattr(state, "last_question", None) or {}
    if (question_type in {"out_of_scope", "food_choice_guidance", "food_allowed"} and not u.slot_fill and not foods and len(cleaned.split()) <= 8
            and MORE.search(cleaned) and last.get("type") in RECIPE_FLOW):
        question_type = last["type"]                       # "what else" after a recipe answer: the same request, without what was shown
        u.more_request = True
        u.ingredients = list(getattr(state, "ingredients_on_hand", []) or [])
        u.foods = list(u.ingredients)
        u.exclude_recipes = sorted(set(u.exclude_recipes) | set(getattr(state, "recipes_shown", []) or []))
        u.recipe_ids = []
    last_type = (getattr(state, "last_question", None) or {}).get("type")
    if question_type in {"out_of_scope", "food_choice_guidance", "food_allowed"} and not u.slot_fill and last_type in RECIPE_FLOW and len(whole.split()) <= 8:
        leave = TARGET_EXCLUDE.match(whole)               # "except onion" / "without onion" after a recipe answer: the same request, no recipe with onion
        left_out = [m.food_id for m in index.find(leave.group("x"), already_clean=True)] if leave else []
        if left_out:
            question_type = last_type
            u.exclude_foods = left_out
            u.ingredients = [f for f in (getattr(state, "ingredients_on_hand", []) or []) if f not in left_out]
            u.foods = list(u.ingredients)
            u.recipe_ids = []
    if question_type in {"meal_from_ingredients", "recipe_request"} and INGREDIENTS.search(cleaned):
        have_clause = cleaned[INGREDIENTS.search(cleaned).end():]
        absent = negated_foods(have_clause, index)                     # "I have paneer but no onion": onion is not in the kitchen
        if absent:
            u.absent_foods = sorted(set(u.absent_foods) | absent)
            u.ingredients = [f for f in u.ingredients if f not in absent]
            u.foods = [f for f in u.foods if f not in absent]
    if question_type in {"neutral_nutrient_lookup", "food_allowed", "protocol_permission", "personal_suitability"}:
        u.subject_kind = "nutrient" if question_type == "neutral_nutrient_lookup" else "allowed"
        u.subject = mentions[0].alias if mentions else ""
        if not u.subject and dish_finder:
            hits = dish_finder(focus)
            u.subject = hits[0]["span"] if hits else ""
        u.subject_nutrient = canonical_nutrient(cleaned)
    u.type = question_type
    return u
