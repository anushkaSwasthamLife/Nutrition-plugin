"""Text cleaning and whole-word phrase matching (stage 4a).

Plain substring search is never used: "dal" must not match "medallion".
"""
from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s'-]")
_SPACE_RE = re.compile(r"\s+")


def clean(text: str, spelling_map: dict[str, str] | None = None) -> str:
    """Lower-case, strip accents and punctuation, collapse spaces, map spelling variants word by word."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch) and unicodedata.category(ch) != "Cf")   # accents and invisible format marks
    text = text.lower().replace("’", "'").replace("–", " ").replace("—", " ")
    text = _PUNCT_RE.sub(" ", text)
    text = text.replace("-", " ")
    words = _SPACE_RE.sub(" ", text).strip().split(" ")
    if spelling_map:
        words = [spelling_map.get(w, w) for w in words]
    return " ".join(w for w in words if w)


def has_phrase(cleaned_text: str, phrase: str) -> bool:
    """True if `phrase` (already cleaned) occurs in `cleaned_text` as whole words."""
    if not phrase:
        return False
    return re.search(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", cleaned_text) is not None


def find_phrases(cleaned_text: str, phrases) -> list[str]:
    return [p for p in phrases if has_phrase(cleaned_text, p)]


def word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w.strip()])
