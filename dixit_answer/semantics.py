"""Meaning of the words around a food (stage 4b): what is a food, what is only a wish or a time, and what the person does NOT have.

"I have paneer, something quick for dinner" used to report "quick" as a food we do not know, and "paneer but no onion" counted onion
as in the kitchen. Three layers, each safe on its own:

1. A closed list of everyday non-food words (wishes, tastes, times, people, requests, English and Roman Hindi). Words on it are never
   a pantry item and never sent to the dietitian queue. The list holds no food: tests check it against the food list.
2. Negation. "no onion", "without onion", "don't have onion", "onion nahi hai", "ran out of onion": the foods named there are
   NOT on hand. They are taken out of what the person has and are not reported as unknown.
3. Optional meaning check for what is left (a word we still do not know). With the AI switched on it is asked one closed question
   about one short plain word: not a food / a food / the same as one food on our list. Its answer only ever (a) drops a word that is
   not a food, or (b) asks the person to confirm a food from OUR list. It never adds a food, and no number comes from it.
   With no AI, or when it is unsure, the word is handled exactly as before.
"""
from __future__ import annotations

import re

from .textnorm import clean

NON_FOOD_WORDS = frozenset("""
quick quickly fast faster stale old overripe rotten spoiled organic homemade easy easily simple simply spicy spice mild tasty tasty yummy delicious healthy healthier healthiest light lighter heavy low high less
much many lot lots crispy soft hot cold warm instant different new good best better nice great tangy sweetish bland filling filling interesting special
kids kid child children family husband wife mom mother dad father guests guest people person everyone someone friends friend baby elders
today tonight tomorrow tomorrows today's tomorrow's yesterday yesterdays morning evening afternoon night dinner lunch breakfast snack snacks meal meals brunch
week weekend day days time now later soon again always never sometimes often daily
want wants wanted need needs needed like likes love prefer feel feeling wish try trying
make making made cook cooking cooked prepare preparing give giving suggest suggestion suggestions tell show help helps please pls plz thanks thank
can could would should will shall may might must what which how why when where who whom whose
any anything something nothing everything else other others more most only just also still even very really quite too so but because since as if then than
from to for with without in on at by about around instead over under after before during between
idea ideas option options recipe recipes dish dishes food foods item items stuff things thing sabji sabzi curry style type kind
this that these those it its they them he she you your our my me us i am is are was were be been being do does did done get got gets going go goes
left leftover leftovers extra spare available handy ready ok okay yes no not dont don't doesn't cant can't cannot wont won't
weight loss diet healthy calorie calories protein carbs nutrition nutritious
ek do teen kuch thoda thodi bahut jaldi achha accha acchi achhi acha tasty banao banado bana bano batao bataiye dikhao dijiye chahiye chahta chahti
hai hain ho hoga hogi tha thi hoon hu mere mera meri mujhe paas ghar mein me ko ka ki ke se par aur ya kal aaj abhi phir nahi nahin
bachche bacche baccha pati patni mehman subah shaam raat dopahar khana nashta sirf bas wala wali wale kaisa kaise kya kyun koi kuchh sab saath bina liye lie
""".split())

# a clause that starts like this says the foods in it are NOT on hand
NEGATED = re.compile(
    r"^(?:(?:but|and|also|plus|though|however)\s+)?(?:(?:i|we)\s+)?"
    r"(?:(?:do not|don't|dont|doesn't|never)\s+have|have no|have not|haven't|havent|has no|am out of|are out of|out of|ran out of|run out of|"
    r"finished|no more|no|not|without|except)\b\s*(?P<x>.*)$")
NEGATED_AFTER = re.compile(r"\b(?:nahi|nahin|khatam|khatm|finished)\b")
SPLIT = re.compile(r"\b(?:but|plus|for|from|because|since|so|while|although|though|however|ki|jo|lekin|magar)\b|"
                   r"(?=\b(?:without|except|no|not|don't|dont|do not|doesn't|never|have no|have not|haven't|out of|ran out of|run out of|finished|no more)\b)")
_PLAIN = re.compile(r"[a-z' ]{2,30}")


def split_chunks(clause: str) -> list[str]:
    """The words after "I have ..." cut into one statement each: at but / for / from / because ... and before a negation word
    ("paneer but no onion" -> "paneer", "no onion"). "and" / "or" do not cut: "no onion or tomato" is one statement."""
    return [p.strip() for p in SPLIT.split(clause) if p and p.strip()]


def negated_foods(clause: str, index) -> set[str]:
    """Food ids the person says they do NOT have: "no onion", "without onion", "don't have onion", "onion nahi hai"."""
    absent: set[str] = set()
    for chunk in split_chunks(clause):
        m = NEGATED.match(chunk)
        if m:
            absent |= {mention.food_id for mention in index.find(m.group("x"), already_clean=True)}
            continue
        after = NEGATED_AFTER.search(chunk)
        if after:                                         # "aloo tamatar nahi hai": the last food before "nahi" is the one that is gone
            before = index.find(chunk[:after.start()], already_clean=True)
            if before:
                absent.add(before[-1].food_id)
    return absent


def meaningful_words(chunk: str, stop_words) -> list[str]:
    """The words of a chunk that could name a food: wishes, tastes, times, people, requests and numbers are taken out."""
    stops = set(stop_words) | NON_FOOD_WORDS
    return [w for w in chunk.split() if w not in stops and not w.isdigit()]


def is_plain_word(phrase: str) -> bool:
    return bool(_PLAIN.fullmatch(phrase))


class SemanticJudge:
    """Asks the language model, if there is one, what a still-unknown word means. Returns ("not_food"|"food"|"same", food_id) or None."""

    def __init__(self, llm, index, enabled: bool = True):
        self.llm, self.index, self.enabled = llm, index, enabled
        self.cache: dict[str, tuple[str, str] | None] = {}
        self.asked = 0

    def judge(self, phrase: str) -> tuple[str, str] | None:
        phrase = clean(phrase)
        if not self.enabled or not is_plain_word(phrase) or getattr(self.llm, "name", "mock") == "mock" or not hasattr(self.llm, "classify_word"):
            return None
        if phrase in self.cache:
            return self.cache[phrase]
        result = None
        try:
            self.asked += 1
            raw = self.llm.classify_word(phrase, sorted(self.index.foods))
        except Exception:                       # no network, rate limit, odd reply: the word is handled as before (and asked again next time)
            return None
        if isinstance(raw, dict):
            label, same = raw.get("label"), raw.get("same_as")
            if label == "not_food":
                result = ("not_food", "")
            elif label == "same_food" and isinstance(same, str) and same in self.index.foods:
                result = ("same", same)           # only ever a food from our own list
            elif label == "food":
                result = ("food", "")
        self.cache[phrase] = result
        return result
