"""Roman Hindi (Hinglish) turned into the English words the rest of the system reads (stage 3b, next to `hindi.py`).

Nothing here answers anything. It rewrites a message, one clause at a time, so that "mujhe chicken khana hai. so what can i have"
reaches the pipeline as "i want chicken. so what can i have", and "meri bhookh lagi hai kya khau" as "i am hungry, what can i have".

1. The message is split into clauses at . ? ! ; , and at "so", "but", "lekin", "magar". English clauses are left exactly as they are.
2. A clause is rewritten only when it holds a marker word from `data/hinglish_glossary.json` that is not also an English word
   ("mujhe", "chahiye", "khana", "kya" ...). That is what keeps "so what can i have" and "main course" safe.
3. A rewritten clause goes through the sentence rules below (SOV order turned round: "X khana hai" -> "i want X"); a clause no rule
   fits has each glossary word swapped and its helper words dropped. A word with no entry stays as written, so a food name still
   reaches the food and dish lookup.
4. Nothing is guessed. The glossary is reviewed by a native speaker, and production refuses it while it is PROPOSED.

The safety check always sees the original text as well (see `pipeline.Engine.answer`).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import DATA_DIR

_GLOSSARY_PATH = DATA_DIR / "hinglish_glossary.json"
_CLAUSE_SPLIT = re.compile(r"[.?!;,\n]+|(?<![\w])(?:so|but|lekin|magar)(?![\w])")
_PUNCT = re.compile(r"[^\w\s']")


@lru_cache(maxsize=1)
def glossary() -> dict:
    data = json.loads(_GLOSSARY_PATH.read_text(encoding="utf-8"))
    data["_markers"] = frozenset(data["markers"]) - frozenset(data.get("weak_markers", []))
    data["_drop"] = frozenset(data["drop"])
    return data


def _words(text: str) -> str:
    """Glossary words swapped for English, helper words dropped, everything else kept. 'main' is "i" unless 'course' follows."""
    g = glossary()
    tokens = text.split()
    out: list[str] = []
    for i, tok in enumerate(tokens):
        if tok in g["_drop"]:
            continue
        if tok == "main" and i + 1 < len(tokens) and tokens[i + 1] == "course":
            out.append(tok)
            continue
        out.append(g["words"].get(tok, tok))
    return " ".join(" ".join(out).split())


_SUBJ = r"(?:mujhe\s+|mujhko\s+|mereko\s+|meri\s+|main\s+|mai\s+)?"
_FIXED = [
    # "iske alawa kuch aur batao", "kuch aur batao", "aur kuch", "koi doosra option": different ones
    (re.compile(r"^(?:ok\s+|accha\s+|theek hai\s+|ab\s+)?(?:iske|isse|is ke)\s+(?:alawa|alaawa|ilawa)(?:\s+\w+){0,3}$"), "something else"),
    (re.compile(r"^(?:ok\s+|accha\s+)?(?:koi\s+)?(?:aur|doosra|dusra|doosre|dusre|alag)\s+(?:kuch|koi|option|options|dish|dishes|batao|bataiye|dikhao)(?:\s+(?:batao|bataiye|dikhao|hai|hai kya|option|options|dish|dishes))*$"), "something else"),
    (re.compile(r"^(?:ok\s+|accha\s+)?kuch\s+(?:aur|doosra|dusra|alag)(?:\s+(?:batao|bataiye|dikhao|hai|hai kya))*$"), "something else"),
    # "mujhe diabetes hai", "mujhe bp hai kya khau" (the question part is cut off first)
    (re.compile(rf"^{_SUBJ}(?P<x>diabetes|thyroid|bp|pcos|pcod|obesity|blood pressure|high blood pressure|cholesterol|uric acid)\s+(?:hai|hain)$"), "i have {x}"),
    # "bhookh lagi hai"
    (re.compile(rf"^{_SUBJ}(?:bhook(?:h)?|bhukh)\s+(?:lagi|lag rahi|lag raha)(?:\s+(?:hai|hain))?$"), "i am hungry"),
    # "mera chicken khane ka mann hai", "chicken khane ka dil hai"
    (re.compile(rf"^(?:mera\s+|meri\s+|{_SUBJ})(?P<x>.+?)\s+kh(?:a|aa)ne\s+(?:ka|ki)\s+(?:mann|man|dil|mood)(?:\s+(?:hai|kar raha hai|kar rahi hai|ho raha hai))?$"), "i want {x}"),
    # "main chicken nahi khata", "mai egg nahi khati"
    (re.compile(rf"^{_SUBJ}(?P<x>.+?)\s+(?:nahi|nahin)\s+(?:khata|khati|khate|khaata|khaati|khaana|khana)(?:\s+(?:hoon|hun|hai))?$"), "i dont eat {x}"),
    # "main chicken khana chahta hoon", "mujhe chicken khana hai", "chicken khani hai", "mujhe chicken chahiye"
    (re.compile(rf"^{_SUBJ}(?P<x>.+?)\s+kh(?:a|aa)n[aie]\s+(?:chahta|chahti)(?:\s+(?:hoon|hun))?$"), "i want {x}"),
    (re.compile(rf"^{_SUBJ}(?P<x>.+?)\s+kh(?:a|aa)n[ai]\s+(?:hai|hain|tha|thi)$"), "i want {x}"),
    (re.compile(rf"^{_SUBJ}(?P<x>.+?)\s+(?:chahiye|chahiye hai)$"), "i want {x}"),
    (re.compile(rf"^{_SUBJ}(?P<x>.+?)\s+(?:chahta|chahti)\s+(?:hoon|hun)$"), "i want {x}"),
    # "chicken banau ya fish"
    (re.compile(r"^(?:aaj\s+|kal\s+)?(?P<x>.+?)\s+(?:banau|banaun|banaye|banayen|pakau|pakaun)\s+ya\s+(?P<y>.+)$"), "i want {x} or {y}, what can i cook"),
    # "chicken healthy hai kya", "kya fish accha hai" (a bare "theek hai" is "ok", not a question)
    (re.compile(r"^kya\s+(?P<x>.+?)\s+(?:healthy|accha|acha|achha|safe|sahi|faydemand)\s+(?:hai|hota hai|hoga|rahega)$"), "is {x} good for me"),
    (re.compile(r"^(?P<x>.+?)\s+(?:healthy|accha|acha|achha|safe|sahi|faydemand)\s+(?:hai|hota hai|hoga|rahega)\s+kya$"), "is {x} good for me"),
]
_TAILS = [
    # "... kya khau", "... kya khaun", "... kya khana chahiye", "... kya kha sakta hoon": the question is "what can i have"
    (re.compile(r"\s*(?:ab\s+|toh\s+)?(?:kya|kaunsa|kaunsi|kaun sa)\s+(?:khau|khaun|khaoon|khaye|khayen|khayein|khana chahiye|lena chahiye|"
                r"kha sakta hoon|kha sakti hoon|kha sakta hun|kha sakti hun|le sakta hoon|le sakti hoon|lu|loon|lun)$"), "what can i have"),
    (re.compile(r"\s*kya\s+(?:banau|banaun|banaye|banayen|pakau|pakaun|bana sakta hoon|bana sakti hoon|bana sakte hain)$"), "what can i cook"),
]
_MEAL_IN = re.compile(r"\b(breakfast|lunch|dinner|nashta|snacks?)\s+(?:mein|me|ke liye)\b")
_FOR = re.compile(r"\s+ke\s+liye\b")


def _is_hindi(clause: str) -> bool:
    return bool(set(clause.split()) & glossary()["_markers"])


_WISH_IN_HEAD = re.compile(r"\b(?:khana|khani|khaana|chahiye|chahta|chahti|mann)\b")
_BARE_REPLY = {"nahi", "nahin", "nahi nahi", "haan", "ha", "ji", "haan ji", "ji haan", "hanji", "ji nahi", "na"}      # yes / no replies the chat reads as they are


def _head(text: str) -> str:
    for pattern, template in _FIXED:
        m = pattern.match(text)
        if m:
            return " ".join(template.format(**{k: _words(v) for k, v in m.groupdict().items()}).split())
    return _words(text)


def _clause(clause: str) -> str:
    text = " ".join(_PUNCT.sub(" ", clause.lower()).split())
    if not text or not _is_hindi(text) or text in _BARE_REPLY:
        return clause.strip()
    text = _MEAL_IN.sub(r"for \1", text)
    text = _FOR.sub(" for", text)
    for pattern, tail in _TAILS:                              # "... kya khau": the question is cut off first, the rest is read on its own
        m = pattern.search(text)
        if m:
            rest = text[:m.start()].strip()
            if tail == "what can i cook" and rest:
                # "X se kya banau" is "what can i cook with X". "aloo pyaz hai kya banau" and "sabji kya banau" are read by the chat as
                # they stand (what is at home, which sabji), so they are left alone; only a clear wish ("chicken khana hai") is turned round.
                with_x = re.match(r"^(?P<x>.+?)\s+se$", rest)
                if with_x:
                    return f"what can i cook with {_words(with_x.group('x'))}"
                if not _WISH_IN_HEAD.search(rest):
                    return clause.strip()
            head = _head(rest) if rest else ""
            return f"{head}, {tail}" if head else tail
    return _head(text)


# Words the older sentence shapes in hindi.py leave in (they keep "x" as written): said the way the rest of the chat reads them.
_POLISH = {"kam": "low", "kum": "low", "zyada": "high", "jyada": "high", "ziyada": "high", "halka": "light", "halki": "light", "kuch": "something",
           "kuchh": "something", "ya": "or"}


def polish(text: str) -> str:
    return " ".join(_POLISH.get(w, w) for w in text.split())


def rewrite(text: str) -> str | None:
    """The message with every Roman-Hindi clause rewritten as English, or None when no clause holds a Hindi marker word.
    The punctuation between clauses is kept ("?" stays "?"), because the chat reads it to tell a question from a statement."""
    if not text:
        return None
    parts = _CLAUSE_SPLIT.split(text)
    glue = [m.group(0).strip() for m in _CLAUSE_SPLIT.finditer(text)]
    out, changed = "", False
    for i, part in enumerate(parts):
        stripped = " ".join(_PUNCT.sub(" ", part.lower()).split())
        if stripped and _is_hindi(stripped):
            new = _clause(part)
            changed = changed or new != part.strip()
        else:
            new = part.strip()
        out += (" " if out and new else "") + new
        if i < len(glue):
            g = glue[i].lower()
            if g in {"so", "but"}:
                out += f" {g}"
            elif g in {"lekin", "magar"}:
                out += " but"
            else:
                out += g[:1] if g[:1] in ".?!;," else "."
    if not changed:
        return None
    return " ".join(out.split())


__all__ = ["glossary", "rewrite", "polish"]
