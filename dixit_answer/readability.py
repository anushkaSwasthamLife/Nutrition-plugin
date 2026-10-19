"""Is an answer plain enough for a person who is not an expert? (INDB plan, phase 5.)

Automatic checks only: reading grade, long sentences, jargon, unit abbreviations. They catch what a machine can catch. Whether a real
person understood the answer is tested with people: see reader_test.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textnorm import clean, has_phrase

_WORD = re.compile(r"[A-Za-z]+(?:'[a-z]+)?")
_SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")
ABBREVIATIONS = ("kcal", "mg", "kj", "g/100g", "rda", "bmi", "gi", "gl")
MAX_GRADE = 8.0
MAX_SENTENCE_WORDS = 25
MIN_WORDS_FOR_GRADE = 30


def syllables(word: str) -> int:
    """Rough syllable count for English words: vowel groups, minus a silent final e."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    count = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee")) and count > 1:
        count -= 1
    return max(1, count)


def _split(text: str) -> tuple[list[str], list[str]]:
    plain = re.sub(r"\([^)]*\)", " ", text)               # a source label in brackets is not part of the sentence
    sentences = [s for s in _SENTENCE_END.split(plain) if _WORD.search(s)]
    words = _WORD.findall(plain)
    return sentences, words


def reading_ease(text: str) -> float | None:
    """Flesch reading ease: 90+ very easy, 60-70 plain English, below 30 very hard. None when there are no words."""
    sentences, words = _split(text)
    if not words or not sentences:
        return None
    syl = sum(syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / len(sentences)) - 84.6 * (syl / len(words))


def grade_level(text: str) -> float | None:
    """Flesch-Kincaid grade: the school year a reader needs."""
    sentences, words = _split(text)
    if not words or not sentences:
        return None
    syl = sum(syllables(w) for w in words)
    return 0.39 * (len(words) / len(sentences)) + 11.8 * (syl / len(words)) - 15.59


@dataclass
class PlainReport:
    grade: float | None
    ease: float | None
    problems: list = field(default_factory=list)

    @property
    def plain(self) -> bool:
        return not self.problems


def check_plain(text: str, jargon: list[str] | None = None) -> PlainReport:
    """What a machine can say about how plain an answer is. Every problem is a reason to rewrite the wording."""
    grade, ease = grade_level(text), reading_ease(text)
    problems = []
    sentences, words = _split(text)
    # The grade formula is unreliable on a line or two: one long dish name ("vegetable biryani") pushes it up. Judge it on 30 words or more.
    if grade is not None and len(words) >= MIN_WORDS_FOR_GRADE and grade > MAX_GRADE:
        problems.append(f"reading grade {grade:.1f} is above {MAX_GRADE:g}")
    for sentence in sentences:
        count = len(_WORD.findall(sentence))
        if count > MAX_SENTENCE_WORDS:
            problems.append(f"a sentence of {count} words: '{sentence.strip()[:50]}...'")
    cleaned = clean(text)
    hits = [w for w in (jargon or []) if has_phrase(cleaned, clean(w))]
    if hits:
        problems.append(f"jargon: {hits}")
    shown = [a for a in ABBREVIATIONS if has_phrase(cleaned, a)]
    if shown:
        problems.append(f"abbreviations a person may not know: {shown}")
    return PlainReport(grade, ease, problems)
