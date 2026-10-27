"""The main assistant (main pipeline, the non-nutrition branch of step R).

Answers greetings, questions about the assistant, and topics outside food (exercise, sleep, medicines, symptoms, anything else).
Every topic has a reviewed reply in `data/router_rules.json`. A real model may word the reply only for the topics in
`model_topics` (medicine and symptom today); greeting, about, exercise, sleep and anything else get the reviewed text, so the
model can never widen what the assistant does ("write me a poem", walking advice). A model reply is refused, and the template sent,
when it is not plain text, is too long, or has any digit (no doses, readings or amounts ever come from here); on medicine, symptom
and general topics a reply naming a change or a dose ("stop", "skip", "dose"...: `model_reply_deny`) is refused too. Medicine and
symptom replies always end with a fixed safety line. Without a model (mock), the template is the reply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .programme import current as programme
from .textnorm import clean, has_phrase

_MARKUP = re.compile(r"[<>{}\[\]|\\`#~]|https?:|www\.|://|@", re.IGNORECASE)
NEVER_MODEL = {"cure", "child"}        # a model must never word a cure or a child's diet answer, whatever the data says


@dataclass
class MainReply:
    text: str
    method: str                                      # template | model
    problems: list = field(default_factory=list)     # why a model reply was refused (audit)


class MainAssistant:
    def __init__(self, llm, rules: dict):
        self.llm = llm
        self.rules = rules
        self.max_words = int(rules["max_words"])
        self.model_topics = set(rules.get("model_topics") or []) - NEVER_MODEL
        deny = rules.get("model_reply_deny") or {}
        self.deny_topics = set(deny.get("topics", []))
        self.deny_words = [clean(w) for w in deny.get("words", [])]
        self.medicine_words = [clean(w) for w in (rules.get("main_topics") or {}).get("medicine", [])]   # any topic that names a medicine

    def reply(self, message: str, topic: str, noted: bool = False) -> MainReply:
        fill = programme().fill                        # the programme's names in the reviewed wording
        template = fill(self.rules["main_replies"].get(topic) or self.rules["main_replies"]["general"])
        locked = fill(self.rules["locked_lines"].get(topic, ""))
        prefix = fill(self.rules["noted_line"]) + " " if noted else ""     # a risk word was read (insulin, pregnancy): say it was kept
        text, method, problems = template, "template", []
        if getattr(self.llm, "name", "mock") != "mock" and topic in self.model_topics:
            try:
                drafted = self.llm.general_reply({"user_message": message[:500], "topic": topic, "max_words": self.max_words})
            except Exception as exc:                 # noqa: BLE001 - any provider failure falls back to the template
                drafted, problems = None, [f"{exc.__class__.__name__}: {str(exc)[:80]}"]
            if isinstance(drafted, str):
                problems = self.problems(drafted, topic)
                if not problems:
                    text, method = " ".join(drafted.split()), "model"
        if locked and locked not in text:
            text = f"{text} {locked}"
        return MainReply(prefix + text, method, problems)

    def problems(self, text: str, topic: str = "") -> list[str]:
        out = []
        if not text.strip():
            out.append("empty")
        if len(text.split()) > self.max_words:
            out.append("too long")
        if any(ch.isdigit() for ch in text):
            out.append("has a number")
        if _MARKUP.search(text) or any(ord(ch) < 32 and ch not in "\n" for ch in text):
            out.append("markup, links or control characters")
        cleaned = clean(text)
        if topic in self.deny_topics or any(has_phrase(cleaned, m) for m in self.medicine_words):   # exercise or sleep: only if a medicine is named
            hits = [w for w in self.deny_words if has_phrase(cleaned, w)]
            if hits:
                out.append(f"names a medicine change or dose: {hits[0]}")
        return out
