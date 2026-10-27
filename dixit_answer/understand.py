"""Understand the message (stage 4): foods, ingredients, conditions, reply-to-our-question, question type.

Cue words in code, checked in a fixed order. The order and cue lists are drafts; the test set in tests/test_understand.py is
the reference. (A small AI tie-break can be added later; not needed for the cue sets here.)
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .dish_lookup import SCAFFOLD as SCAFFOLD_WORDS
from .foods import FoodIndex
from .unknown_foods import compare_sides, food_phrase, kind_question, subject_of
from .targets import QUAL_FIELD, RECIPE_WORDS, WANT, Target, ingredient_wish, is_target_request, parse_qualifiers, parse_targets
from .semantics import HARMFUL_WORDS, HEALTH_WORDS, NON_FOOD_WORDS, NEGATED, NEGATED_AFTER, meaningful_words, negated_foods, split_chunks
from .status import parse_status
from .textnorm import clean, has_phrase

TYPES = [
    "emergency", "followup_reply", "neutral_nutrient_lookup", "neutral_food_comparison", "source_fact_lookup",
    "food_allowed", "protocol_permission", "personal_suitability", "meal_protocol_application",
    "status_dependent_protocol_question", "food_choice_guidance", "meal_from_ingredients", "recipe_request",
    "protocol_explain", "support_struggle", "out_of_scope", "greeting_thanks",
]

CREATIVE = re.compile(r"\b(?:write|compose|draft)\b.*\b(?:poem|poetry|story|essay|song|code|script|letter|email|speech)\b|\b(?:poem|poetry|joke|riddle|lyrics)\b")
PLAN_REQUEST = re.compile(r"\b(?:meal plan|diet plan|diet chart|meal chart|food chart|menu plan|weekly plan|weekly menu|week plan|plan for the week|plan for a week|"
                          r"(?:\d+|seven|7|three|five|ten|14|30) day (?:meal |diet |food )?(?:plan|chart|menu)|full day (?:meal |diet )?plan|daily meal plan)\b")
# Values the nutrition binding excludes from the approved release (prohibited_runtime_fields): never given, never estimated.
EXCLUDED_METRIC = re.compile(r"\b(?:insulin (?:demand|index|load|score)|food insulin index|fii|glyca?emic (?:index|load|value)|"
                             r"(?:gi|gl) (?:of|value|score|for|kya|kitna)|what(?: is|s| s) the (?:gi|gl)|ka (?:gi|gl))\b")
DEFINITION = re.compile(r"^(?:what is|what are|what s|whats|define|meaning of|explain|tell me about|what does)\s+(?:a |an |the )?"
                        r"(?:calorie|calories|kcal|protein|proteins|carb|carbs|carbohydrate|carbohydrates|fat|fats|fibre|fiber|nutrient|nutrients|macro|macros)(?: mean)?$")
COOK_ASK = re.compile(r"\b(?:what (?:to|should i|can i|do i|shall i) (?:cook|make|prepare)|(?:cook|make|prepare) today|what (?:to|can i) (?:cook|make) (?:today|tonight|now|for (?:lunch|dinner|breakfast))|"
                      r"(?:breakfast|lunch|dinner) (?:ideas?|options?|recipes?)|ideas? for (?:breakfast|lunch|dinner)|something to (?:cook|make)|what s for (?:lunch|dinner|breakfast))\b")
GOAL = re.compile(r"\b(?:lose|reduce|gain|drop|cut|shed)\s+(?:\d+(?:\.\d+)?\s*)?(?:kg|kgs|kilo|kilos|kilogram|kilograms|pounds?|lbs|weight|belly|fat)\b|\bweight loss\b|\bget slim\b|\bbecome slim\b")
REQUEST_WORDS = re.compile(r"\b(?:want|need|what|which|how|can i|could|should|suggest|show|recipe|meal|dish|dishes|options?|ideas?)\b")
PORTION = re.compile(r"\bhow (?:much|many)\b.*\b(?:should|can|do|must|shall) (?:i|we)\b.*\b(?:eat|have|drink|take|use|consume|add|put)\b|\b(?:portion|serving) sizes?\b|"
                     r"\bhow (?:much|many)\b.*\bper (?:meal|day)\b|\bhow (?:much|many)\b.*\b(?:is|are) enough\b|"
                     r"\bhow (?:much|many)\b.*\b(?:do|should|must) (?:i|we)\b.*\b(?:need|require|get)\b")
SYMPTOM = re.compile(r"\b(?:headache|headaches|dizzy|dizziness|nausea|nauseous|vomit|vomiting|fever|pain|aching|weak|weakness|tired|tiredness|fatigue|giddy|cramps?|"
                     r"constipation|diarrhoea|diarrhea|bloating|bloated|acidity|gas|rash|itching)\b")
STATEMENT_DIET = re.compile(r"^(?:i am|i'm|i m|im|we are|we're|i eat|we eat|my diet is|i follow)\b")
STATEMENT_ALLERGY = re.compile(r"\b(?:lactose intolerant|lactose intolerance|gluten intolerant|gluten intolerance|gluten free|celiac|coeliac|dairy free|no dairy|no gluten|"
                               r"without gluten|no lactose|nut allergy|peanut allergy|egg allergy|soy allergy)\b")
STATEMENT_VEGAN = re.compile(r"\b(?:i am|i'm|i m|im|we are|we're)\s+(?:a\s+)?(?:vegan|plant based)\b|^(?:vegan|plant based)$")
STATEMENT_AVOID = re.compile(r"^(?:i|we)\s+(?:do not|don't|dont|don t|never|cannot|can't|can t|cant)\s+(?:eat|have|take|like|touch)\s+(?P<x>.+)$|"
                             r"^(?:i|we)\s+(?:avoid|skip|stay away from)\s+(?P<y>.+)$|^no\s+(?P<z>.+?)\s+for\s+me$")
GREETING = re.compile(r"^(hi|hello|hey|namaste|good (morning|evening|afternoon|night)|thanks?|thank you|thank you so much|ok|okay|"
                      r"ok thanks|okay thanks|thx|ty|bye|goodbye|good bye|see you|see you later|take care|bye bye)( (dixit|coach|so much|a lot|there))?"
                      r"( how are you| how r u| how are you doing| kaise ho| kya haal hai)?$")
ABOUT_ME = re.compile(r"^(?:who|what) (?:are|r) (?:you|u)$|^what can (?:you|u) do$|^(?:can you |could you |please )?help(?: me)?$|^what (?:is|s) your name$|^your name$|"
                      r"^are you (?:a |an )?(?:real |human |robot|bot|ai|doctor|dietitian|dietician|person|nutritionist|machine|chatbot)\b.*|^how can you help.*|^what do you do$|"
                      r"^who made you$|^who built you$|^are you (?:there|listening)$")
BODY_INFO = re.compile(r"\b(?:my )?(?:weight|height|age|bmi) (?:is|=)\s*\d|\b\d{1,3}(?:\.\d)?\s*(?:kg|kgs|kilos?|cm|feet|ft|inches|inch)\b|"
                       r"^i (?:am|m) (?:a )?(?:man|woman|male|female|boy|girl|lady|guy)$|\bi (?:am|m) \d{1,3} (?:years? old|yrs?|yo)\b|\bi (?:am|m) \d{1,3}$|\bmy bmi\b")
SPECIAL_DIET = re.compile(r"\b(?:keto|ketogenic|paleo|atkins|carnivore|raw food diet|intermittent fasting|fasting|fast|fasts|vrat|upvas|upavas|navratri|ekadashi|ramadan|ramzan|"
                          r"roza|iftar|suhoor|sehri|mahashivratri|janmashtami|diwali fast|karwa chauth|detox|juice cleanse|water fast|dry fast)\b")
NO_ALLERGY = re.compile(r"^(?:i have |i got |we have )?(?:no|zero|none|nil)(?: known)?(?: food)? allerg(?:y|ies)(?: at all)?$|^no allergies$|^not allergic to anything$|"
                        r"^i am not allergic to anything$")
INGREDIENTS = re.compile(r"\b((?:cook|make|prepare|recipes?|dishes?|meals?|ideas?)\s+(?:with|using|from)|(?<!can )(?<!could )(?<!may )(?<!should )(?<!do )(?<!shall )(?<!must )(?<!would )(?<!will )i have|i've got|i got|we have|at home (i|we) have|there is|there's|have only|only have|my paas|ingredients?\s+(?:are\s+)?(?!of\b|in\b|for\b|used\b|needed\b|required\b|list\b))\b")
HINDI_MAKE = re.compile(r"\b(bana|banau|banaun|banaye|pakau|pakaun|pakana|pakaye|kya bana)\b")   # not "banana": that's a fruit
DISH_AFTER = re.compile(r"(?:recipe (?:for|of)|how (?:do|to|can|should) (?:i |you |we )?(?:make|cook|prepare)|make|cook|prepare)\s+(.+)$")
DISH_BEFORE = re.compile(r"^(?:(?:the|a|an|please|pls|give me|show me|send me|share)\s+)*(.+?)\s+(?:recipe|recipes|ki recipe|ka recipe|kaise banaye|kaise banate hai|kaise banta hai)(?:\s+please|\s+pls)?$")
DISH_FILLER = {"please", "pls", "show", "me", "the", "a", "an", "recipe", "recipes", "for", "i", "want", "give", "ka", "ki", "ke", "ko", "dikhao", "batao",
               "chahiye", "of", "to", "and", "ok", "okay", "yes", "this", "that", "one", "sure", "make", "how"}
GENERIC_DISH = {"recipes", "recipe", "some recipes", "a recipe", "something", "food", "a dish", "dish", "dishes", "some dishes",
                "me recipes", "it", "this", "that", "meal", "meals", "lunch", "dinner"}
MAKE = re.compile(r"\b(make|prepare|cook|dish|meal|recipe|what can i (make|cook|prepare))\b")
RECIPE = re.compile(r"\b(recipe|how (do|to|can) (i |you )?(make|cook|prepare)|how is .* made|method to (make|cook)|want to (?:make|cook|prepare)|let s (?:make|cook)|how about making)\b")
SOURCE = re.compile(r"\b(source|where (does|do|did) (this|that|these|the) .*(come|from)|where is (this|that) from|reference)\b")
LOOKUP_MORE = re.compile(r"\b(?:sugar|sugars|sodium)\s+(?:in|of|content)\b|\bhow (?:much|many) (?:sugar|sodium)\b|\b(?:nutrition|nutrients?|macros?)\s+(?:facts?\s+)?(?:of|in|for)\b|"
                         r"\b(?:nutrition|nutritional) (?:facts|value|values|info|information|details)\b")
LOOKUP = re.compile(r"\b(how (many|much)|what is the|what's the|amount of)\b.*\b(calorie|carb|protein|fat|fiber|sugar|energy|"
                    r"nutrition|nutrients?)\b|\b(calorie|carb|protein|fat|fiber)s? (in|of)\b")
COMPARE = re.compile(r"\b(compare|comparison|vs|versus|difference between|better than|which is better|healthier)\b")
STATUS = re.compile(r"\b(non diabetic|no longer diabetic|not diabetic anymore|reversed|reversal|now normal|sugar is normal)\b")
PERSONAL = re.compile(r"\b(safe for me|good for me|ok for me|okay for me|suitable for me|right for me|bad for me|harmful for me|"
                      r"(?:good|bad|ok|okay|safe|fine|healthy|harmful|suitable|allowed) for (?:diabetics?|diabetes|weight loss|losing weight|bp|blood pressure|thyroid|pcos|pregnancy|pregnant women|kids|children|elders))\b")
MEAL_PROTOCOL = re.compile(r"\b(\d{1,2}\s*(?:am|pm)|after \d{1,2}|before \d{1,2}|late night|midnight|55 minute|eat first|what comes first|what to eat first|steps? of (?:a |the )?meal|"
                           r"order of (?:the |a )?meal|meal order|meal sequence|nuts before|before salad|what time|which time|how long|three times|thrice|3 times|four times|how often|"
                           r"eat late|eating late|late dinner|if i eat late|water with meals|drink water|water between|water during|water after|water before|between meals|in between|snack|snacks|snacking|skip (breakfast|lunch|dinner|a meal)|two meals|"
                           r"2 meals|55 min|55 minutes|meal timing|when should i eat|how many meals|third meal|three meals|"
                           r"every (two|three|2|3|4|four|few) hours|small meals|frequent meals|small frequent|five meals|six meals|"
                           r"eat more often|eating more often|more meals)\b")
FREQUENCY = re.compile(r"\b(every (two|three|2|3|4|four|few) hours|small meals|frequent meals|small frequent|five meals|six meals|"
                       r"eat more often|eating more often|more meals|three meals|how many meals)\b")
PERMISSION = re.compile(r"\b(allowed|permitted|in the program|in the protocol|as per the program|does the program allow)\b")
ALLOWED = re.compile(r"\b(can i (eat|have|take|drink)|should i (eat|have)|is it ok to (eat|have)|may i (eat|have))\b")
CHOICE = re.compile(r"\b(cheat (?:day|meal)|treat day|binge|what (should|can|do) i (eat|have|cook)|what to eat|which (sabji|vegetable|food|foods|dal|fruit)|"
                    r"what (sabji|vegetable|food|foods)|what (kind of|type of) food|instead of|eat to (reduce|lose|control)|"
                    r"diet (for|to)|foods? (for|to) (reduce|lose|control)|what is good to eat|suggest (some )?(food|sabji|vegetable))\b")
EXPLAIN = re.compile(r"\b(how does (the |this |dixit )?(program|programme|diet|plan|lifestyle) work|what is (the )?dixit|explain (the |this |dixit )?"
                     r"(program|programme|rules|diet|plan)|what are the rules|what are (the |dixit )?(program|programme|dixit) rules|(program|programme|dixit) rules|"
                     r"rules of (the )?(program|programme|diet)|tell me about (the )?(program|programme|dixit))\b")
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
NEED_AMOUNT = re.compile(r"\bhow (?:much|many)\b.*\b(?:do|should|must) (?:i|we)\b.*\b(?:need|require)\b|\bhow (?:much|many)\b.*\b(?:do|does) (?:i|we|a person|an adult)\b.*\bneed\b")
TIME_WISH = re.compile(r"\b(?:in|within|under|less than)\s+(?:\d+|ten|five|fifteen|twenty|thirty)\s*(?:min|mins|minutes?|hours?)\b|\b(?:quick|quickly|fast|instant|jaldi)\b")
# "other than this", "something else", "what other options do i have": a request for different ones, said in up to nine words.
# (No bare "more" or "else": "more protein" is a wish, not a request for other dishes.)
MORE_ASK = re.compile(r"\b(?:what else|anything else|something else|anything different|something different|any other|more options|other options|another one|one more|"
                      r"show more|different ones?|other ones?|what other|which other|other than (?:this|these|that|those|them|the above|above|what you (?:showed|said|gave|listed))|"
                      r"different from (?:this|these|that|those|them)|not (?:this|these|those))\b")
BARE_WHAT = re.compile(r"^(?:(?:ok|okay|so|and|then|but|hmm)\s+)*what (?:can|should|do|could) i (?:have|eat|take)(?:\s+(?:then|now|instead|today|else))?$")
# "why?", "tell me more", "where is that from": only the written reason or source of the last answer is given, never a new claim
EXPLAIN_FOLLOW = re.compile(r"^(?:ok |okay |but |and |so )?(?:why|why so|why is that|why is it|why is that so|how come|tell me more|more details|more info|"
                            r"more information|explain|explain that|explain it|explain more|explain please|what do you mean|how so|where is that from|"
                            r"where is this from|where does that come from|where does this come from|source|sources|how do you know|reason|the reason|"
                            r"what is the reason|why please|why is this)$")
# "is dal high in protein", "does paneer have much fibre": a question about one nutrient of a food
NUTRIENT_QUESTION = re.compile(r"\b(?:is|are|does|do|has|have)\b.*\b(?:high|low|rich|good source|much|enough|any|lot of|lots of)\b.*"
                               r"\b(?:calorie|calories|carb|carbs|carbohydrate|carbohydrates|protein|proteins|fat|fats|fibre|fiber)\b")
ADD_FOODS = re.compile(r"^(?:and also|also|and|plus|with|i also have|we also have|also have|i have also|there is also|and i have|and we have)\s+\w+")
WHAT_CAN_MAKE = re.compile(r"\b(?:what|which) (?:can|could|shall|should) (?:i|we) (?:make|cook|prepare)\b|\bwhat to (?:make|cook)\b|\banything (?:i can|to) (?:make|cook)\b")
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
    recipe_nutrients: str = ""                           # a recipe of ours whose nutrition is asked ("calories in jeera aloo")
    preference: str = ""
    recipe_ids: list = field(default_factory=list)       # approved recipes named in the message
    target_exclude: list = field(default_factory=list)   # words to leave out of a dish search ("paneer")
    target_diet: str = ""                                # vegetarian | jain | egg (narrower) or non_vegetarian (dishes with meat, fish or egg) for this search
    target_include: list = field(default_factory=list)  # meat / fish words the dish search must contain ("chicken")
    meat_recipe: bool = False                            # it was a recipe request for that meat: no approved recipe, so dishes are listed and the dietitian told
    extra_foods: list = field(default_factory=list)      # other foods named with the meat ("chicken and onion"): not used to pick dishes
    time_wish: bool = False                              # "what can i make in 10 minutes": we hold no cooking times
    target_mode: str = "new"                             # new | refine (changed amounts or something left out) | more (what else) | cleared
    targets: list = field(default_factory=list)          # nutrient amounts asked for ("326 kcal", "26 g protein"): a dish search
    compare_sides: list = field(default_factory=list)    # the foods an "X vs Y" question sets side by side, as phrases
    statement: dict = field(default_factory=dict)        # what the person said about themselves: {"allergies", "diet", "avoid", "label"}
    reading: float | None = None                         # a blood sugar reading the person shared (mg/dL)
    refuse_plan: bool = False                            # asked for a meal plan or diet chart: answered with a plain "I can't", then the programme
    excluded_metric: bool = False                        # asked for insulin demand, GI, GL or FII: not approved values (binding check NUT-FIELD-001)
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
    if ABOUT_ME.match(cleaned):
        return "about_assistant"
    if NO_ALLERGY.match(cleaned):
        return "out_of_scope"                              # read as a statement below
    if BODY_INFO.search(cleaned) and not LOOKUP.search(cleaned) and not NUTRIENT_WORDS.search(cleaned):
        return "body_info"
    if CREATIVE.search(cleaned) or set(cleaned.split()) & HARMFUL_WORDS:
        return "out_of_scope"                              # a poem, a joke, something harmful: not a food question
    if DEFINITION.match(cleaned):
        return "definition_question"
    if PORTION.search(cleaned) and not LOOKUP.search(cleaned):
        return "portion_question"
    if COOK_ASK.search(cleaned) and not INGREDIENTS.search(cleaned) and not LOOKUP.search(cleaned):
        return "recipe_request"                            # "what to cook today": a list of dishes
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
    if NEED_AMOUNT.search(cleaned) and not foods:
        return "portion_question"                          # "how much protein do i need a day": the programme sets no amounts
    if LOOKUP.search(cleaned) or NUTRIENT_QUESTION.search(cleaned) or LOOKUP_MORE.search(cleaned):
        return "neutral_nutrient_lookup"
    if COMPARE.search(cleaned) and len(foods) >= 2:
        return "neutral_food_comparison"
    if STATUS.search(cleaned):
        return "status_dependent_protocol_question"
    if PERSONAL.search(cleaned):
        return "personal_suitability"
    if EXPLAIN.search(cleaned):
        return "protocol_explain"
    if (STRUGGLE.search(cleaned) or SYMPTOM.search(cleaned)) and not ALLOWED.search(cleaned):
        return "support_struggle"        # "I feel hungry between meals" is a struggle, not a timing question
    if MEAL_PROTOCOL.search(cleaned):
        return "meal_protocol_application"
    if SPECIAL_DIET.search(cleaned) and not LOOKUP.search(cleaned) and not ALLOWED.search(cleaned):
        return "special_diet"
    if foods and len(cleaned.split()) <= 4 and NUTRIENT_WORDS.search(cleaned) and not COMPARE.search(cleaned):
        return "neutral_nutrient_lookup"                   # "dal protein", "idli kcal"
    if CHOICE.search(cleaned) or ((GOAL.search(cleaned) or PLAN_REQUEST.search(cleaned)) and not (INGREDIENTS.search(cleaned) and foods)):
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
DIET_SWITCH = re.compile(r"^(?:(?:and|but|ok|okay|now|then|please)\s+)*(?:what about|how about|only|just|show|give me|i want|i eat|make it|i am|im)?\s*(?:pure |only |just )?"
                         r"(?:veg|vegetarian|non veg|nonveg|non vegetarian|egg|eggetarian|jain)(?:\s+(?:food|dishes|dish|options|please|only|one|ones))*$")
TARGET_EXCLUDE = re.compile(r"^(?:(?:but|and|also|ok|okay|please|now|then)\s+)*(?:except(?: for)?|excluding|without|no|not|other than|apart from|besides|skip|"
                            r"avoid|leave out|remove|minus)\s+(?P<x>.+?)(?:\s+(?:please|pls))?$")
# A follow-up that starts like this adds to the search ("but i need less calories"); a full request without it starts a new one.
CONTINUE_LEAD = re.compile(r"^(?:but|and|also|plus|now|then|ok|okay|so|still|what about|how about|and also|but also|same|same but|same with)\b")
CONTINUE_ANY = re.compile(r"\b(?:also|too|as well|along with|on top|in addition|still)\b")
MEAL_NOUN = re.compile(r"\b(?:meal|meals|dish|dishes|food|foods|recipe|recipes|options?|what can i (?:have|eat|cook|make))\b")
START_OVER = re.compile(r"^(?:(?:ok|okay|please|now)\s+)*(?:start over|start again|new search|clear (?:the |my )?search|clear (?:it|that|everything|all)|"
                        r"forget (?:it|that|this|everything|all of it|the search|my search)|reset|never mind (?:that|it|the search))(?:\s+(?:please|pls))?$")
# B: taking one part out of the search. The chips on the chat page send these ("forget high protein", "forget non veg", "forget without paneer").
FORGET = re.compile(r"^(?:(?:ok|okay|and|but|also|now|then|please)\s+)*(?:forget|drop|remove|clear|cancel|undo|no need for|dont need|don't need|do not need|"
                    r"never mind)\s+(?:about\s+)?(?:the\s+)?(?P<x>.+?)(?:\s+(?:please|pls|filter|wish|part|one))?$")
TARGET_FIELD_WORDS = re.compile(r"\b(protein|proteins|carb|carbs|carbohydrate|carbohydrates|fat|fats|fibre|fiber|calories|calorie|kcal|energy)\b")
SEARCH_TURNS = 8                     # a search stays open this long, unless another kind of question ends it first (pipeline)
EXCLUDE_FILLER = {"the", "any", "anything", "and", "or", "of", "a", "an", "all", "dishes", "dish", "with", "in", "it", "that", "those", "food", "foods", "thanks", "thank", "you", "need", "more", "ok", "okay", "yes", "now", "today", "worries", "problem"}


def target_refinement(text: str, state, index: FoodIndex, word_lists: dict):
    """A follow-up to the last dish search, read as the same search with a change; None when it is not one.

    The search is one frame that follows-ups change: new nutrient wishes are added ("but i need less calories" keeps "high
    protein"), a wish for the same nutrient replaces the old one, a diet narrows it ("non veg"), leave-outs pile up. Only a full
    request that names a meal or dish, with no "but" / "and" / "also" in front, starts a new search; "start over" clears it."""
    last = getattr(state, "last_targets", None)
    include = list(getattr(state, "target_include", []) or [])
    if not (last or include or getattr(state, "target_open", False)) or state.turn - getattr(state, "last_targets_turn", -99) > SEARCH_TURNS:
        return None
    cleaned = clean(question_part(text), word_lists["spelling_map"])
    words = cleaned.split()
    if not words or len(words) > 14:
        return None
    recent = state.turn - getattr(state, "last_targets_turn", -99) <= RECENT_TURNS
    targets = [Target(*t) for t in last]
    exclude = list(getattr(state, "target_exclude", []) or [])
    diet_now = getattr(state, "target_diet", "") or ""
    mode = None
    new_diet = None
    if START_OVER.match(cleaned):
        targets, exclude, include, new_diet, mode = [], [], [], "", "cleared"
    elif FORGET.match(cleaned) and _forget(FORGET.match(cleaned).group("x"), targets, exclude, word_lists, index, include) is not None:
        targets, exclude, new_diet, include = _forget(FORGET.match(cleaned).group("x"), targets, exclude, word_lists, index, include)
        mode = "refine" if (targets or include) else "cleared"
    else:
        cut = TARGET_EXCLUDE.match(cleaned)
        new = parse_targets(cleaned)
        new += [t for t in parse_qualifiers(cleaned) if t.field not in {n.field for n in new}]
        diet = _preference(cleaned, word_lists)
        wish = ingredient_wish(cleaned, follow_up=True)
        fresh = MEAL_NOUN.search(cleaned) and new and not CONTINUE_LEAD.match(cleaned) and not CONTINUE_ANY.search(cleaned)
        if diet and DIET_SWITCH.match(cleaned):
            mode, new_diet = "refine", diet                  # "what about veg", "only vegetarian", "non veg please": the same search for another diet
        elif cut and not new and not MORE_ASK.search(cleaned):        # "other than these" asks for different dishes, it leaves out no food
            tokens = [w for w in cut.group("x").split() if w not in EXCLUDE_FILLER]
            if tokens:
                names = set(tokens)
                for mention in index.find(" ".join(tokens), already_clean=True):      # "curd" also leaves out dahi, "potato" also aloo
                    food = index.foods[mention.food_id]
                    names |= {a for a in [food["name"], *food.get("aliases", [])] if " " not in a}
                exclude += sorted(n for n in names if n not in exclude)
                include = [w for w in include if w not in names and w.rstrip("s") not in {n.rstrip("s") for n in names}]
                mode = "refine"
        elif (new or wish) and not fresh:
            merged = {t.field: t for t in targets}
            merged.update({t.field: t for t in new})         # the same nutrient again replaces it ("make it 30 g protein instead")
            targets, mode = list(merged.values()), "refine"
            if wish:                                         # "and fish" adds to the meats asked for, "fish instead" replaces them
                include = sorted(set(include) | set(wish)) if (CONTINUE_LEAD.match(cleaned) or CONTINUE_ANY.search(cleaned)) else wish
            if diet:
                new_diet = diet                              # "i need non veg with less calories": both changes at once
        elif ((MORE.search(cleaned) and len(words) <= 5) or (MORE_ASK.search(cleaned) and len(words) <= 10)
              or (BARE_WHAT.match(cleaned) and recent)) and not index.find(cleaned, already_clean=True):
            mode = "more"
    if mode is None:
        return None
    u = Understanding(cleaned=cleaned, type="target_meal_search", focus=cleaned)
    u.targets, u.target_exclude, u.target_mode, u.target_include = targets, exclude, mode, include
    u.target_diet = diet_now if new_diet is None else _search_diet(new_diet)
    if new_diet:
        u.preference, u.statement = new_diet, {"diet": new_diet, "label": "diet"}
    return u


DIET_DISH_ASK = re.compile(r"\b(?:suggest|show|give|tell|list|recommend|batao|dikhao)\b.*\b(?:dishes|dish|meals|meal|food|foods|options|ideas|recipes|recipe)\b|"
                           r"^(?:veg|vegetarian|non veg|nonveg|non vegetarian|jain|egg|eggetarian)\s+(?:dishes|dish|meals|food|foods|options|ideas)$")


def _diet_dishes(cleaned: str, word_lists: dict) -> str:
    """"suggest non veg dishes", "show some veg meals": a request for dishes by diet alone, with no nutrient and no meat. The diet, or ""."""
    if not DIET_DISH_ASK.search(cleaned):
        return ""
    return _preference(cleaned, word_lists)


def _search_diet(diet: str) -> str:
    """The diet a search keeps to: a narrower one, or non-veg meaning dishes with meat, fish or egg in them."""
    return diet if diet in {"vegetarian", "jain", "egg", "non_vegetarian"} else ""


def _forget(x: str, targets: list, exclude: list, word_lists: dict, index: FoodIndex, include: list):
    """Take one part out of the search: a nutrient wish, the diet, a meat asked for or a leave-out. (targets, exclude, diet or None,
    include) or None when the words name none of them (then "remove paneer" is read as leaving paneer out, as before)."""
    x = x.strip()
    fields = {QUAL_FIELD.get(m.group(1), "") for m in TARGET_FIELD_WORDS.finditer(x)} - {""}
    left_out = re.match(r"^(?:without|except|no)\s+(?P<w>.+)$", x)
    if left_out:
        gone = set(left_out.group("w").split())
        for mention in index.find(left_out.group("w"), already_clean=True):     # leaving out curd also left out dahi: both come back
            food = index.foods[mention.food_id]
            gone |= {a for a in [food["name"], *food.get("aliases", [])] if " " not in a}
        kept = [w for w in exclude if w not in gone]
        return (targets, kept, None, include) if len(kept) < len(exclude) else None
    if fields:
        kept = [t for t in targets if t.field not in fields]
        return (kept, exclude, None, include) if len(kept) < len(targets) else None
    if _preference(x, word_lists) or x in {"diet", "veg filter", "diet filter"}:
        return targets, exclude, "", include
    names = {w.rstrip("s") for w in x.split()}
    if include and names & {w.rstrip("s") for w in include}:
        return targets, exclude, None, [w for w in include if w.rstrip("s") not in names]
    return None


def _statement(cleaned: str, index: FoodIndex, allergy_reader, word_lists=None) -> dict:
    """What the person says about themselves, as a change to their profile: allergies, a vegan diet, foods they do not eat. {} when it is a question."""
    if "?" in cleaned or len(cleaned.split()) > 14:
        return {}
    out: dict = {}
    if NO_ALLERGY.match(cleaned):
        return {"allergies": [], "label": "none"}
    if STATEMENT_VEGAN.search(cleaned):
        return {"diet": "vegetarian", "allergies": ["milk", "egg"], "label": "vegan"}
    if STATEMENT_ALLERGY.search(cleaned) and allergy_reader:
        found = allergy_reader(cleaned)
        if found:
            return {"allergies": sorted(found), "label": "intolerance"}
    if STATEMENT_DIET.search(cleaned) and word_lists is not None:
        diet = _preference(cleaned, word_lists)
        if diet:
            return {"diet": diet, "label": "diet"}
    m = STATEMENT_AVOID.search(cleaned)
    if m:
        what = m.group("x") or m.group("y") or m.group("z") or ""
        foods = [mention.food_id for mention in index.find(what, already_clean=True)]
        found = (allergy_reader(what) or []) if allergy_reader else []
        if foods or found:
            out = {"avoid": foods, "allergies": sorted(found), "label": "avoid"}
    return out


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
    clause = re.split(r"\b(?:i am|am|i'm|i m|im|allergic|allergy|allergies)\b", clause)[0]     # a statement about the person is not a food
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


ORDINALS = {"first": 1, "1st": 1, "pehla": 1, "pehli": 1, "pahla": 1, "pahli": 1, "second": 2, "2nd": 2, "dusra": 2, "dusri": 2,
            "doosra": 2, "doosri": 2, "third": 3, "3rd": 3, "teesra": 3, "teesri": 3, "tisra": 3, "tisri": 3, "fourth": 4, "4th": 4,
            "chautha": 4, "chauthi": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6}
CHOICE_FILLER = {"the", "one", "number", "no", "option", "please", "pls", "show", "me", "wala", "wali", "vala", "vali", "dish", "recipe", "that",
                 "i", "want", "will", "take", "give", "ok", "okay"}


def choice_number(cleaned: str, count: int) -> int:
    """1-based pick from a list of `count` choices: "2", "the second one", "number 3", "last one", "dusra wala". 0 = not a pick.
    Only a short reply made of a number or ordinal and filler words counts, so "2 rotis" is not a pick."""
    words = cleaned.split()
    picks = [w for w in words if w not in CHOICE_FILLER]
    if len(picks) != 1 or len(words) > 6:
        return 0
    word = picks[0]
    n = count if word in {"last", "aakhri", "akhri"} else ORDINALS.get(word) or (int(word) if word.isdigit() else 0)
    return n if 1 <= n <= count else 0


def read_reply(cleaned: str, slot: str, foods: list[str], index: FoodIndex, word_lists: dict,
               allergy_reader=None, recipe_ids=None, choices=None) -> dict:
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
        if recipe_ids:
            return {"recipe_choice": recipe_ids[0]}
        n = choice_number(cleaned, len(choices or []))          # "2", "the second one", "dusra": a pick from the list we showed
        return {"recipe_choice": choices[n - 1]} if n else {}
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


TYPO_VOCAB = ("calories", "calorie", "protein", "proteins", "carbs", "carbohydrate", "carbohydrates", "nutrition", "vegetarian", "breakfast", "recipe", "allergic",
              "allergy", "diabetes", "diabetic", "dinner", "lunch", "fibre", "fiber", "vegetable", "vegetables", "chapati", "potato", "tomato", "banana")


def _typo_vocab(index: FoodIndex) -> tuple[set, list]:
    """The words a mistyped word may be repaired to: nutrient and question words and the single-word food names (cached on the food index)."""
    cached = getattr(index, "_typo_cache", None)
    if cached is None:
        words = set(TYPO_VOCAB) | {a for a in index.alias_to_id if " " not in a and len(a) >= 5}
        cached = (words, sorted(words))
        index._typo_cache = cached
    return cached


def fix_typos(cleaned: str, index: FoodIndex) -> str:
    """"calries in poha" -> "calories in poha": a long word that is not a known word but is one letter or two from a nutrient or food word."""
    known, vocab = _typo_vocab(index)
    skip = SCAFFOLD_WORDS | NON_FOOD_WORDS
    out = []
    for word in cleaned.split():
        if len(word) >= 5 and word not in known and word not in skip and not word.isdigit():
            close = difflib.get_close_matches(word, vocab, n=1, cutoff=0.84)
            if close and close[0][0] == word[0] and close[0].rstrip("s") != word.rstrip("s") and not close[0].startswith(word) and not word.startswith(close[0]):
                word = close[0]
        out.append(word)
    return " ".join(out)


def understand(text: str, state, index: FoodIndex, word_lists: dict, recipe_finder=None, allergy_reader=None,
               dish_finder=None, word_judge=None, food_probe=None) -> Understanding:
    again = target_refinement(text, state, index, word_lists)
    if again is not None:
        return again
    text = carry_over(text, state, index, word_lists, dish_finder)
    cleaned = fix_typos(clean(text, word_lists["spelling_map"]), index)
    focus = fix_typos(clean(question_part(text), word_lists["spelling_map"]), index)
    excluded_dishes: list = []
    whole = cleaned
    cut = EXCLUDE.search(cleaned)
    if cut and cut.group("x").split()[0] in PRONOUNS | {"above"}:
        cut = None                                              # "other than this / these": different ones, not a food to leave out
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
        u.slot_fill = read_reply(cleaned, last["slot"], foods, index, word_lists, allergy_reader, u.recipe_ids,
                                 getattr(state, "recipe_choices", None))

    question_type = classify(cleaned, foods, index)
    if EXCLUDED_METRIC.search(cleaned):
        rest = classify(" ".join(EXCLUDED_METRIC.sub(" ", cleaned).split()), foods, index)
        if rest in {"neutral_nutrient_lookup", "neutral_food_comparison"}:
            question_type = rest                           # "calories and GI of dal": the calories are answered, the GI declined
    u.frequency_question = bool(FREQUENCY.search(cleaned)) and question_type == "meal_protocol_application"
    if question_type == "out_of_scope" and not u.slot_fill and parse_status(cleaned):
        question_type = "status_dependent_protocol_question"       # "yes, my consultant confirmed it": a status statement
    if u.recipe_ids and question_type in {"food_allowed", "food_choice_guidance", "meal_from_ingredients"} and "?" not in text:
        # A dish name on its own ("Moong Dal Chilla", "show me jeera aloo please") is a request for that recipe.
        leftover = set(getattr(recipe_finder, "__self__", None).last_leftover) if hasattr(recipe_finder, "__self__") else set()
        if leftover <= DISH_FILLER:
            question_type = "recipe_request"
    leftover = set(getattr(getattr(recipe_finder, "__self__", None), "last_leftover", None) or [])
    dish = " ".join(w for w in cleaned.split() if w not in leftover)
    if (u.recipe_ids and canonical_nutrient(cleaned) and question_type in {"neutral_nutrient_lookup", "food_allowed", "out_of_scope"}
            and hasattr(recipe_finder, "__self__") and dish not in index.alias_to_id        # "carbs in bhindi" is the food, not our sabji
            and all(w in ASK_FILLER or NUTRIENT_WORD_RE.fullmatch(w) for w in leftover)):
        question_type, u.recipe_nutrients = "neutral_nutrient_lookup", u.recipe_ids[0]    # the whole dish, not its words one by one
        u.subject, u.subject_kind = dish, "nutrient"                                       # "and protein?" next
    if question_type != "recipe_request" and not (u.slot_fill and "recipe_choice" in u.slot_fill):
        u.recipe_ids = []                # a dish name only counts when the person is asking for that recipe
    if question_type == "recipe_request" and TIME_WISH.search(cleaned):
        u.time_wish = True
    if question_type == "recipe_request" and not u.recipe_ids:
        m = DISH_AFTER.search(cleaned) or DISH_BEFORE.match(cleaned)          # "recipe for methi paratha", "methi paratha recipe"
        if m:
            dish = " ".join(w for w in m.group(1).split() if w not in {"please", "pls", "some", "a", "an", "the", "me", "for", "my"})
            if TIME_WISH.search(dish):
                u.time_wish = True                                        # "in 10 minutes", "quick": no dish named, and we hold no cooking times
            elif dish and dish not in GENERIC_DISH and not u.foods and any(w not in NON_FOOD_WORDS for w in dish.split()):
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
    if dish_finder and question_type == "meal_from_ingredients" and not u.slot_fill and foods and ingredient_wish(cleaned) and not u.recipe_ids:
        # "what can i cook with chicken and onion": the meat picks the dishes; the other foods are said not to
        wish_now = ingredient_wish(cleaned)
        u.targets, u.target_include, u.type = is_target_request(cleaned), wish_now, "target_meal_search"
        u.meat_recipe = bool(RECIPE_WORDS.search(cleaned))
        u.extra_foods = [index.foods[f]["name"] for f in foods if index.foods[f]["category"] != "pantry"][:3]
        u.target_diet = _search_diet(u.preference)
        return u
    if question_type not in {"support_wellbeing", "greeting_thanks", "followup_reply"} and not u.slot_fill and not foods and not u.recipe_ids:
        targets = is_target_request(cleaned)
        wish = ingredient_wish(cleaned) if dish_finder and question_type in {"out_of_scope", "food_choice_guidance", "target_meal_search", "recipe_request", "meal_from_ingredients"} else []
        diet_dishes = _diet_dishes(cleaned, word_lists) if dish_finder and question_type in {"out_of_scope", "food_choice_guidance", "recipe_request"} and not wish else ""
        named = bool(dish_finder and any(h["exact"] for h in dish_finder(focus))) and not (wish and RECIPE_WORDS.search(cleaned))
        if (targets or wish or diet_dishes) and not named:       # (a recipe request for a meat dish, "fish curry recipe", is still a meat wish)       # a loose word match ("drink") is not a dish named
            question_type, u.targets = "target_meal_search", targets      # "a meal with 326 kcal and 26 g protein": dishes near those values
            u.target_include = wish                                       # "i want chicken": dishes with chicken in them
            u.meat_recipe = bool(wish) and bool(RECIPE_WORDS.search(cleaned))
            u.target_diet = _search_diet(u.preference or diet_dishes)     # "suggest non veg dishes": a search by diet alone
            u.type = question_type
            return u
    if ((question_type in {"out_of_scope", "food_choice_guidance"} or (question_type == "source_fact_lookup" and (getattr(state, "last_explain", None) or {}).get("kind") == "recipe"))
            and not u.slot_fill and EXPLAIN_FOLLOW.match(cleaned)
            and getattr(state, "last_explain", None) and state.turn - getattr(state, "last_explain_turn", -99) <= RECENT_TURNS):
        u.type = "explain_last"
        return u
    if question_type not in {"emergency", "greeting_thanks", "followup_reply", "support_wellbeing"} and not u.slot_fill:
        statement = _statement(cleaned, index, allergy_reader, word_lists)         # "i am lactose intolerant", "i am vegan", "i don't eat onion"
        if statement:
            u.statement = statement
            if not REQUEST_WORDS.search(cleaned) and question_type in {"out_of_scope", "food_choice_guidance", "food_allowed", "protocol_permission"}:
                question_type = "profile_statement"                    # only a statement: acknowledged. With a question in it, the question is answered too
    if PLAN_REQUEST.search(cleaned) and question_type == "food_choice_guidance":
        u.refuse_plan = True
    if EXCLUDED_METRIC.search(cleaned):
        u.excluded_metric = True
    if question_type == "out_of_scope" and not u.slot_fill and INGREDIENTS.search(cleaned) and not u.conditions and not (HARMFUL_WORDS | HEALTH_WORDS) & set(cleaned.split()) and not SYMPTOM.search(cleaned) and not subject_of(cleaned):
        unknown_pantry, _s, _a = pantry_words(scrub_conditions(cleaned, word_lists), index, word_lists.get("ingredient_stop_words", []))
        if foods or unknown_pantry:
            question_type = "meal_from_ingredients"        # "i have only oats": what is at home, even when we do not know it
    elif question_type == "out_of_scope" and not u.slot_fill and (HEALTH_WORDS & set(cleaned.split())) and not LOOKUP.search(cleaned) and not REQUEST_WORDS.search(cleaned):
        question_type = "safety_ack"                       # "i have heart disease": thanked and noted, never read as a food
    if question_type in {"out_of_scope", "food_choice_guidance", "neutral_nutrient_lookup", "food_allowed", "neutral_food_comparison"} and not u.slot_fill \
            and not u.recipe_ids and (question_type != "neutral_food_comparison" or len(foods) < 2):
        sides = compare_sides(cleaned)
        if len(sides) >= 2 and len(foods) < 2:
            question_type, u.compare_sides = "phrase_comparison", sides         # "oats vs poha", "brown rice vs white rice"
    if question_type == "out_of_scope" and not u.slot_fill and kind_question(cleaned):
        question_type = "food_choice_guidance"            # "are nuts allowed", "can i eat fruits": a kind of food, answered from the programme
    if question_type in {"out_of_scope", "food_choice_guidance"} and not u.slot_fill and not foods and not u.recipe_ids:
        subject = subject_of(cleaned)                       # "can i eat pizza", "is coffee ok": a food that is not on the approved list
        if subject:
            question_type, u.unknown_food_query = "unknown_food_question", subject
    if question_type == "out_of_scope" and not u.slot_fill and food_probe and 1 <= len(cleaned.split()) <= 4 and not (HARMFUL_WORDS | HEALTH_WORDS) & set(cleaned.split()):
        phrase = food_phrase(cleaned)
        verdict = word_judge(phrase) if phrase and word_judge and not food_probe(phrase) else None
        if phrase and set(cleaned.split()) - set(phrase.split()) <= {"and", "in", "of", "for", "the", "a", "an", "on", "about", "what", "is"} and re.fullmatch(r"[A-Za-z' -]+", whole.strip()) and not {"ignore", "instructions", "previous", "system", "prompt"} & set(cleaned.split()) and (food_probe(phrase) or (verdict and verdict[0] in {"food", "same"})):
            question_type = "neutral_nutrient_lookup"        # "jalebi", "momos", "poha upma": a food or dish asked in a word or two (we hold data, or the meaning check says it is a food)
    last = getattr(state, "last_question", None) or {}
    if (question_type in {"out_of_scope", "food_choice_guidance", "food_allowed"} and not u.slot_fill and not foods and len(cleaned.split()) <= 8
            and (MORE.search(cleaned) or MORE_ASK.search(cleaned)) and last.get("type") in RECIPE_FLOW):
        question_type = last["type"]                       # "what else" after a recipe answer: the same request, without what was shown
        u.more_request = True
        u.ingredients = list(getattr(state, "ingredients_on_hand", []) or [])
        u.foods = list(u.ingredients)
        u.exclude_recipes = sorted(set(u.exclude_recipes) | set(getattr(state, "recipes_shown", []) or []))
        u.recipe_ids = []
    if (question_type in {"out_of_scope", "food_choice_guidance"} and not u.slot_fill and not foods and not u.more_request
            and len(cleaned.split()) <= 10 and MORE_ASK.search(cleaned)):
        question_type = "more_unclear"                     # "something else" with nothing before it to continue: ask, never explain the programme
    last_type = (getattr(state, "last_question", None) or {}).get("type")
    on_hand = list(getattr(state, "ingredients_on_hand", []) or [])
    if last_type in RECIPE_FLOW and on_hand and not u.slot_fill and len(whole.split()) <= 8:
        if foods and ADD_FOODS.match(whole) and question_type in {"out_of_scope", "food_allowed", "food_choice_guidance", "meal_from_ingredients"}:
            u.ingredients = list(dict.fromkeys([*on_hand, *foods]))              # "i also have onion", "and tomato": added to what was said before
            u.foods = list(u.ingredients)
            u.recipe_ids = []
            question_type = "meal_from_ingredients"
        elif question_type == "recipe_request" and not foods and not u.recipe_ids and (COOK_ASK.search(whole) or WHAT_CAN_MAKE.search(whole)):
            u.ingredients = on_hand                                               # "what can i make" after saying what is at home
            u.foods = list(on_hand)
            question_type = "meal_from_ingredients"
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
    if u.recipe_nutrients:
        u.subject_nutrient = canonical_nutrient(cleaned)                # the subject stays the whole dish, set above
    elif question_type in {"neutral_nutrient_lookup", "food_allowed", "protocol_permission", "personal_suitability"}:
        u.subject_kind = "nutrient" if question_type == "neutral_nutrient_lookup" else "allowed"
        u.subject = mentions[0].alias if mentions else ""
        if not u.subject and dish_finder:
            hits = dish_finder(focus)
            u.subject = hits[0]["span"] if hits else ""
        u.subject_nutrient = canonical_nutrient(cleaned)
    u.type = question_type
    return u
