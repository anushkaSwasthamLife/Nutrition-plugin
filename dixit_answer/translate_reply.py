"""Answers in Hindi and Roman Hindi (Hinglish) (stage 9, after every check has passed on the English answer).

The answer is always built and checked in English first. This module only changes the language of the finished text, and only by
one of two routes, so nothing is ever written here that was not already approved:

1. **Fixed text.** Emergency, harmful-intent, check-in and fallback replies have reviewed Hindi wording in `data/reply_translations.json`
   (matched on the exact English sentence; if the English changes, the entry stops matching and the English is shown). Those replies
   skip the model entirely, and are shown with the English reply below them.
2. **Model translation, then code checks.** Everything else is translated line by line. A line is accepted only if it keeps the same
   numbers, the same locked sentences, the same bold and bullet marks, adds no food that was not in the English line, has no links,
   and is in the right script. A failed line gets one retry with the reasons; then the English answer is shown.

Which language: Devanagari in the message gives Hindi; Roman Hindi that `hindi.py` had to rewrite gives Hinglish; a later short message
("ok", "yes") keeps the language of the chat; an English message of four words or more switches back to English.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from .config import DATA_DIR
from .hindi import has_devanagari, translate as hindi_translate
from .textnorm import clean

LANGUAGES = {"en", "hi", "hinglish"}
PROTECTED_TYPES = {"emergency", "harmful_intent"}      # fixed reviewed text only, never the model; the English follows below
NO_MODEL_TYPES = PROTECTED_TYPES | {"support_wellbeing"}
SWITCH_BACK_WORDS = 4                                   # an English message this long ends a Hindi chat
_TOKEN = re.compile(r"\{\{[A-Z]+\d*\}\}")
_DIGITS = re.compile(r"\d+")
_DEV = re.compile(r"[ऀ-ॿ]")
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)
_MARKUP = re.compile(r"https?:|www\.|://|<\s*/?[a-zA-Z!]|!\[|\]\(|javascript:|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)])\s")
_DEV_DIGITS = re.compile(r"[०-९]")


@lru_cache(maxsize=1)
def reply_translations() -> dict:
    return json.loads((DATA_DIR / "reply_translations.json").read_text(encoding="utf-8"))


def detect_language(original: str, rewritten: str, previous: str = "en") -> str:
    """The language this message is written in, given what the chat has used so far."""
    if has_devanagari(original):
        return "hi"
    if rewritten != original:
        return "hinglish"
    if previous in {"hi", "hinglish"} and len(re.findall(r"[A-Za-z']+", original)) < SWITCH_BACK_WORDS:
        return previous
    return "en"


@dataclass
class Translation:
    text: str
    language: str                                       # the language shown ("en" when the English answer stayed)
    method: str = "none"                                # none | fixed | fixed+english | model | english_fallback
    problems: list = field(default_factory=list)       # why the model route was refused (audit)


class ReplyTranslator:
    def __init__(self, llm, index, words: dict):
        self.llm, self.index, self.words = llm, index, words
        self.data = reply_translations()
        self.fixed = sorted(((v["english"], v) for v in self.data["fixed"].values() if v.get("english")),
                            key=lambda pair: -len(pair[0]))

    @property
    def version(self) -> str:
        return self.data["version"]

    # ------------------------------------------------------------------
    def translate(self, english: str, language: str, plan_type: str, user_message: str = "") -> Translation:
        if language not in {"hi", "hinglish"} or not english.strip():
            return Translation(english, "en")
        protected, used = self._swap_fixed(english, language)
        if plan_type in PROTECTED_TYPES:
            if used and not _TOKEN.sub("", protected).strip():       # the whole answer is reviewed text
                return Translation(f"{self._put_back(protected, used)}\n\n{english}", language, "fixed+english")
            return Translation(english, "en", "english_fallback", ["no reviewed wording for this reply"])
        if plan_type in NO_MODEL_TYPES:
            return Translation(english, "en", "english_fallback", ["wellbeing replies are not machine translated"])
        if not _letters_outside_tokens(protected):                    # only fixed sentences: nothing for the model to do
            return Translation(self._put_back(protected, used), language, "fixed")
        translate_lines = getattr(self.llm, "translate_lines", None)
        if translate_lines is None or getattr(self.llm, "name", "mock") == "mock":
            return Translation(english, "en", "english_fallback", ["no translation model"])
        lines = protected.split("\n")
        todo = [i for i, line in enumerate(lines) if _letters_outside_tokens(line)]
        problems: list = []
        for attempt in range(2):
            try:
                out = translate_lines([lines[i] for i in todo], language, problems or None)
            except Exception as exc:                                  # noqa: BLE001 - any provider failure: the English answer stands
                return Translation(english, "en", "english_fallback", [f"{exc.__class__.__name__}: {str(exc)[:80]}"])
            problems = self.check([lines[i] for i in todo], out, language)
            if not problems:
                result = list(lines)
                for i, text in zip(todo, out):
                    result[i] = " ".join(text.split()) if not _BULLET.match(text) else text.strip()
                return Translation(self._put_back("\n".join(result), used), language, "model")
        return Translation(english, "en", "english_fallback", problems)

    # ------------------------------------------------------------------
    def _swap_fixed(self, text: str, language: str) -> tuple[str, dict]:
        used: dict = {}
        for english, entry in self.fixed:
            if english in text and entry.get(language):
                token = "{{F%d}}" % (len(used) + 1)
                used[token] = entry[language]
                text = text.replace(english, token)
        return text, used

    @staticmethod
    def _put_back(text: str, used: dict) -> str:
        for token, value in used.items():
            text = text.replace(token, value)
        return text

    def check(self, source: list, out, language: str) -> list:
        """Reasons the translation is not accepted (empty = accepted). Numbers and locked sentences come from the source, never the model."""
        if not isinstance(out, list) or len(out) != len(source) or not all(isinstance(x, str) for x in out):
            return ["the translation did not return one line for each line"]
        problems = []
        for n, (src, got) in enumerate(zip(source, out), 1):
            tag = f"line {n}"
            if not got.strip():
                problems.append(f"{tag}: empty")
                continue
            if _TOKEN.findall(src) != _TOKEN.findall(got):
                problems.append(f"{tag}: locked items {_TOKEN.findall(src)} were not kept exactly once, in order")
            if sorted(_DIGITS.findall(_TOKEN.sub(" ", src))) != sorted(_DIGITS.findall(_TOKEN.sub(" ", got))) or _DEV_DIGITS.search(got):
                problems.append(f"{tag}: the numbers changed (write digits 0-9 exactly as in the English)")
            if bool(_BULLET.match(src)) != bool(_BULLET.match(got)) or src.count("**") != got.count("**"):
                problems.append(f"{tag}: bullet or bold marks changed")
            if _MARKUP.search(got):
                problems.append(f"{tag}: links or markup")
            if len(got) > 2.5 * len(src) + 40:
                problems.append(f"{tag}: much longer than the English")
            body = _TOKEN.sub(" ", got)
            if language == "hinglish" and _DEV.search(body):
                problems.append(f"{tag}: write Roman letters only, no Devanagari")
            if language == "hi" and len(re.findall(r"[A-Za-z]+", _TOKEN.sub(" ", src))) >= 4 and not _mostly_devanagari(body):
                problems.append(f"{tag}: write in Devanagari Hindi")
            extra = self._foods(got) - self._foods(src)
            if extra:
                problems.append(f"{tag}: names foods that are not in the English line: {sorted(self.index.name(f) for f in extra)}")
        return problems

    def _foods(self, text: str) -> set:
        roman = hindi_translate(_TOKEN.sub(" ", text)) if has_devanagari(text) else _TOKEN.sub(" ", text)
        cleaned = clean(roman, self.words["spelling_map"])
        return {m.food_id for m in self.index.find(cleaned, already_clean=True)}


def _letters_outside_tokens(line: str) -> bool:
    return bool(_LETTERS.search(_TOKEN.sub(" ", line)))


def _mostly_devanagari(text: str) -> bool:
    letters = _LETTERS.findall(text)
    return not letters or len(_DEV.findall(text)) / len(letters) >= 0.5
