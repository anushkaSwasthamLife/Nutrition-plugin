"""Intent router (main pipeline step R): is this message nutrition related? Runs after the emergency and safety checks.

Nutrition related (handed to the nutrition plugin): foods, dishes, recipes, nutrients, meals, the programme's food rules, and
statements about allergies, diet or conditions. Not nutrition (the main assistant answers): symptoms, medicines on their own,
exercise, sleep, greetings, anything else. Unsure (a condition or health word with no food or diet word): one question is asked,
and the reply decides where the waiting message goes. Word lists are in `data/router_rules.json`; no AI is used.

Order: a reply to the router's own question · a medicine change or a cure question (even with food words: they get the fixed
medicine line, never food advice) · food, dish or nutrition words (a misspelt one too) · a condition statement · a
yes/no reply to the plugin's last question · a greeting or a main topic · any other reply to the plugin's last question · a short
follow-up to a nutrition answer · unsure words · otherwise the main assistant.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .textnorm import clean, has_phrase
from .understand import ABOUT_ME, GREETING

NUTRITION, MAIN, UNSURE = "nutrition", "main", "unsure"
THANKS_WORDS = {"thanks", "thank", "thx", "ty", "bye", "goodbye", "take", "see", "ok", "okay"}
REPLY_STARTS = {"none", "nothing", "i don't", "i don t", "i dont", "i do", "i take", "i'm not", "i m not", "i am not"}   # with clarify_yes / clarify_no


@dataclass
class Route:
    kind: str                       # nutrition | main | unsure
    intent: str                     # why: food | dish | nutrition_words | condition_statement | plugin_reply | continuation |
                                    #      clarified | greeting | thanks | about | exercise | sleep | medicine | symptom | general | unsure
    message: str = ""               # the message to answer, when it is not this one (a clarified earlier message)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "intent": self.intent, "resolved_earlier_message": bool(self.message)}


class IntentRouter:
    def __init__(self, rules: dict, index, word_lists: dict, recipe_aliases, dish_finder=None):
        self.rules = rules
        self.index = index
        self.recipe_aliases = recipe_aliases           # cleaned recipe names and aliases
        self.dish_finder = dish_finder                 # cleaned text -> list of dish mentions (INDB), or None
        self.nutrition_words = [clean(w) for w in rules["nutrition_words"]]
        self.starts = [clean(w) for w in rules["statement_starts"]]
        self.unsure_words = [clean(w) for w in rules["unsure_words"]]
        self.topics = {topic: [clean(w) for w in words] for topic, words in rules["main_topics"].items()}
        self.yes = {clean(w) for w in rules["clarify_yes"]}
        self.no = {clean(w) for w in rules["clarify_no"]}
        self.conditions = [clean(w) for words in word_lists["conditions"].values() for w in words]
        self.max_follow = int(rules["continuation_max_words"])
        self.single_words = [w for w in self.nutrition_words if " " not in w and len(w) >= 5]   # for misspellings: "calries"
        self.not_typos = {clean(w) for w in rules.get("not_typos", [])}                        # real words: "waiter" is not "water"
        alt = lambda words: "|".join(re.escape(clean(w)) for w in sorted(words, key=len, reverse=True)) or r"\0"
        change, meds = rules.get("medicine_change") or {}, alt(self.topics.get("medicine", []))
        gap = rf"(?:\s+(?:{alt(change.get('between', []))}))*\s+"      # only small words between: "stop taking my bp medicine"
        self.medicine_change = re.compile(rf"(?<!\w)(?:{alt(change.get('verbs_before', []))}){gap}(?:{meds})(?!\w)|"
                                          rf"(?<!\w)(?:{meds}){gap}(?:{alt(change.get('verbs_after', []))})(?!\w)")
        self.cure_words = [clean(w) for w in rules["cure_words"]]
        self.cure_context = self.conditions + self.nutrition_words + [clean(w) for w in rules["cure_context"]]
        child = rules["child"]
        self.child_words, self.child_ask, self.cook_words = ([clean(w) for w in child[k]] for k in ("words", "ask_words", "cook_words"))
        self.child_age = re.compile(child["age_pattern"])

    @property
    def version(self) -> str:
        return self.rules["version"]

    def route(self, text: str, state, original: str = "") -> Route:
        """`text` is the message as the plugin reads it (Hinglish words put into English); `original` is what was typed, so the
        medicine, cure and child checks also see "metformin band kar du" and "bachche ko kya khilau" as written."""
        cleaned = clean(text, self.index.spelling_map)          # spelling variants as the food index reads them
        both = [cleaned] + ([clean(original)] if original and original != text else [])
        pending = state.route_pending
        if pending and pending.get("turn") == state.turn:      # the reply to "is this about food?"
            answer = cleaned.rstrip(" .!")
            if answer in self.yes:
                return Route(NUTRITION, "clarified", pending.get("message", ""))
            if answer in self.no:
                return Route(MAIN, "clarified", pending.get("message", ""))
            # anything else is a new message: the question is dropped
        if any(self.medicine_change.search(c) for c in both):
            return Route(MAIN, "medicine")                    # "can I stop metformin if I follow this diet": never food advice
        if any(any(has_phrase(c, w) for w in self.cure_words) and any(has_phrase(c, w) for w in self.cure_context) for c in both):
            return Route(MAIN, "cure")                        # "will this diet cure my diabetes": no promise, fixed reply
        if any(self._child(c) for c in both):
            return Route(MAIN, "child")                       # "what should my 5 year old eat": the programme is for adults
        if self.index.find(cleaned, already_clean=True):
            return Route(NUTRITION, "food")
        if self._dish(cleaned):
            return Route(NUTRITION, "dish")
        if any(has_phrase(cleaned, w) for w in self.nutrition_words) or self._misspelt(cleaned):
            return Route(NUTRITION, "nutrition_words")
        condition = any(has_phrase(cleaned, w) for w in self.conditions)
        if condition and any(cleaned == s or cleaned.startswith(s + " ") for s in self.starts):
            return Route(NUTRITION, "condition_statement")    # "I have diabetes": noted for the food answers
        followup = state.last_followup or {}
        plugin_asked = (followup.get("turn") == state.turn and state.last_route == NUTRITION) or state.pending_confirm
        if plugin_asked and self._reply_start(cleaned):
            return Route(NUTRITION, "plugin_reply")           # "no, I don't take any medicine" answers the plugin's medicine question
        main = self._main_topic(cleaned)
        if main:
            return Route(MAIN, main)                          # even with a plugin question open: "how much should I walk" is not a reply
        if plugin_asked:
            return Route(NUTRITION, "plugin_reply")           # "none", "veg", "moong" after the plugin's own question
        if state.last_route == NUTRITION and len(cleaned.split()) <= self.max_follow:
            return Route(NUTRITION, "continuation")           # "why?", "what else", "the second one" after a nutrition answer
        if condition or any(has_phrase(cleaned, w) for w in self.unsure_words):
            return Route(UNSURE, "unsure")
        return Route(MAIN, "general")

    def names_food(self, text: str, state) -> bool:
        """A food or dish in the message, or "it" / "this" right after a food was talked about."""
        cleaned = clean(text, self.index.spelling_map)
        if self.index.find(cleaned, already_clean=True) or self._dish(cleaned) or any(has_phrase(cleaned, w) for w in self.conditions):
            return True                                       # "what should I do about my diabetes": the plugin answers for the condition
        recent = state.last_subject and state.turn - state.last_subject_turn <= 3
        return bool(recent and set(cleaned.split()) & {"it", "this", "that", "these", "those", "them"})

    def _child(self, cleaned: str) -> bool:
        """What a child should eat or be given, not cooking for the family ("what can I make for my kids")."""
        ages = [int(m.group(1)) / (12 if m.group(2).startswith(("month", "mahine")) else 1) for m in self.child_age.finditer(cleaned)]
        if ages and min(ages) >= 18:
            return False                                      # "my son is 25 years old": an adult
        return ((ages or any(has_phrase(cleaned, w) for w in self.child_words))
                and any(has_phrase(cleaned, w) for w in self.child_ask) and not any(has_phrase(cleaned, w) for w in self.cook_words))

    def _reply_start(self, cleaned: str) -> bool:
        return any(cleaned == w or cleaned.startswith(w + " ") for w in self.yes | self.no | REPLY_STARTS)

    def _misspelt(self, cleaned: str) -> bool:
        return any(close[:2] == word[:2]                     # same start: "looking" is not "cooking"
                   for word in cleaned.split() if len(word) >= 5 and word not in self.not_typos
                   for close in difflib.get_close_matches(word, self.single_words, n=1, cutoff=0.85))

    def _main_topic(self, cleaned: str) -> str:
        if GREETING.match(cleaned):
            return "thanks" if set(cleaned.split()) & THANKS_WORDS else "greeting"
        if ABOUT_ME.match(cleaned):
            return "about"
        for topic, words in self.topics.items():
            if any(has_phrase(cleaned, w) for w in words):
                return topic
        return ""

    def _dish(self, cleaned: str) -> bool:
        padded = f" {cleaned} "
        if any(alias and f" {alias} " in padded for alias in self.recipe_aliases):
            return True
        return bool(self.dish_finder and self.dish_finder(cleaned))
