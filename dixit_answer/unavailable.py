"""Fixed replies for the two cases where the system cannot do its job safely, and the audit log that must not stop an emergency.

Both replies are plain English, fixed text, with no food in them and no claim that anyone was told. They do not depend on any data
file, because the reason they are shown may be that the data files could not be loaded.

Proposed policy (27 Oct 2026, for a doctor and the developer to confirm; see workflow.html, "If something is missing"):
  * the limits or rules cannot be loaded  -> no recipe and no food advice; the reply below; the next message tries to load again
  * the audit log cannot be written       -> emergency and harmful-intent replies always go out and their log lines wait in memory;
                                             other answers go out too while up to MAX_QUEUED lines wait; after that the answer is
                                             withheld (the reply below) until the log can be written again
"""
from __future__ import annotations

EMERGENCY_LINE = "If this is an emergency, call 112 now. If you are thinking of harming yourself, call Tele-MANAS on 14416 (free, 24 hours)."

LIMITS = ("I can't check your health limits right now, so I can't suggest a recipe or give food advice. Please try again in a few minutes. "
          + EMERGENCY_LINE)
AUDIT = ("I can't keep a record of this conversation right now, so I'm not able to give food advice. Please try again in a few minutes. "
         + EMERGENCY_LINE)

REPLIES = {"limits": LIMITS, "audit": AUDIT}


class AuditUnavailable(RuntimeError):
    """The audit log cannot be written and its waiting list is full: the answer must be withheld."""


def reply(kind: str, conversation_id: str | None = None) -> dict:
    """The chat API's answer for an unavailable system, with every key the page expects."""
    return {"conversation_id": conversation_id or "", "turn": 0, "answer": REPLIES[kind], "type": "unavailable", "plan": {},
            "checks": [], "ai_used": False, "fallback": True, "records": [], "ms": 0, "language": "en", "remembered": [],
            "badge": None, "sources": [], "route": None, "handled_by": "unavailable", "unavailable": kind}
