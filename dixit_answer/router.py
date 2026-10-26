"""Intent router (main pipeline step R): is this message nutrition related? Runs after the emergency and safety checks.

Nutrition related (handed to the nutrition plugin): foods, dishes, recipes, nutrients, meals, the programme's food rules, and
statements about allergies, diet or conditions. Not nutrition (the main assistant answers): symptoms, medicines on their own,
exercise, sleep, greetings, anything else. Unsure (a condition or health word with no food or diet word): one question is asked,
and the reply decides where the waiting message goes. Word lists are in `data/router_rules.json`; no AI is used.

Order: a reply to the router's own question · food, dish or nutrition words · a condition statement · a greeting or a main
topic · a reply to the plugin's last question · a short follow-up to a nutrition answer · unsure words · otherwise the main assistant.
"""
from __future__ import annotations

from dataclasses import dataclass

from .textnorm import clean, has_phrase
from .understand import ABOUT_ME, GREETING

NUTRITION, MAIN, UNSURE = "nutrition", "main", "unsure"
THANKS_WORDS = {"thanks", "thank", "thx", "ty", "bye", "goodbye", "take", "see", "ok", "okay"}


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

    @property
    def version(self) -> str:
        return self.rules["version"]

    def route(self, text: str, state) -> Route:
        cleaned = clean(text, self.index.spelling_map)          # spelling variants as the food index reads them
        pending = state.route_pending
        if pending and pending.get("turn") == state.turn:      # the reply to "is this about food?"
            answer = cleaned.rstrip(" .!")
            if answer in self.yes:
                return Route(NUTRITION, "clarified", pending.get("message", ""))
            if answer in self.no:
                return Route(MAIN, "clarified", pending.get("message", ""))
            # anything else is a new message: the question is dropped
        if self.index.find(cleaned, already_clean=True):
            return Route(NUTRITION, "food")
        if self._dish(cleaned):
            return Route(NUTRITION, "dish")
        if any(has_phrase(cleaned, w) for w in self.nutrition_words):
            return Route(NUTRITION, "nutrition_words")
        condition = any(has_phrase(cleaned, w) for w in self.conditions)
        if condition and any(cleaned == s or cleaned.startswith(s + " ") for s in self.starts):
            return Route(NUTRITION, "condition_statement")    # "I have diabetes": noted for the food answers
        main = self._main_topic(cleaned)
        if main:
            return Route(MAIN, main)                          # even with a plugin question open: "how much should I walk" is not a reply
        followup = state.last_followup or {}
        if (followup.get("turn") == state.turn and state.last_route == NUTRITION) or state.pending_confirm:
            return Route(NUTRITION, "plugin_reply")           # "none", "veg", "yes" after the plugin's own question
        if state.last_route == NUTRITION and len(cleaned.split()) <= self.max_follow:
            return Route(NUTRITION, "continuation")           # "why?", "what else", "the second one" after a nutrition answer
        if condition or any(has_phrase(cleaned, w) for w in self.unsure_words):
            return Route(UNSURE, "unsure")
        return Route(MAIN, "general")

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
