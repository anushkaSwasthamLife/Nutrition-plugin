"""The main assistant (main pipeline, the non-nutrition branch of step R).

Answers greetings, questions about the assistant, and topics outside food (exercise, sleep, medicines, symptoms, anything else).
With a real model it writes a short reply; the reply is refused and the reviewed template from `data/router_rules.json` is sent
instead when it is not plain text, is too long, or has any digit (no doses, readings or amounts ever come from here). Medicine
and symptom replies always end with a fixed safety line. Without a model (mock), the template is the reply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .programme import current as programme

_MARKUP = re.compile(r"[<>{}\[\]|\\`#~]|https?:|www\.|://|@", re.IGNORECASE)
TEMPLATE_ONLY = {"thanks", "cure", "child"}        # a model must never word a cure or a child's diet answer


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

    def reply(self, message: str, topic: str, noted: bool = False) -> MainReply:
        fill = programme().fill                        # the programme's names in the reviewed wording
        template = fill(self.rules["main_replies"].get(topic) or self.rules["main_replies"]["general"])
        locked = fill(self.rules["locked_lines"].get(topic, ""))
        prefix = fill(self.rules["noted_line"]) + " " if noted else ""     # a risk word was read (insulin, pregnancy): say it was kept
        text, method, problems = template, "template", []
        if getattr(self.llm, "name", "mock") != "mock" and topic not in TEMPLATE_ONLY:
            try:
                drafted = self.llm.general_reply({"user_message": message[:500], "topic": topic, "max_words": self.max_words})
            except Exception as exc:                 # noqa: BLE001 - any provider failure falls back to the template
                drafted, problems = None, [f"{exc.__class__.__name__}: {str(exc)[:80]}"]
            if isinstance(drafted, str):
                problems = self.problems(drafted)
                if not problems:
                    text, method = " ".join(drafted.split()), "model"
        if locked and locked not in text:
            text = f"{text} {locked}"
        return MainReply(prefix + text, method, problems)

    def problems(self, text: str) -> list[str]:
        out = []
        if not text.strip():
            out.append("empty")
        if len(text.split()) > self.max_words:
            out.append("too long")
        if any(ch.isdigit() for ch in text):
            out.append("has a number")
        if _MARKUP.search(text) or any(ord(ch) < 32 and ch not in "\n" for ch in text):
            out.append("markup, links or control characters")
        return out
