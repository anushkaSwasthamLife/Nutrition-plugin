"""Chat state (stage 2): what was said, what we asked, what we know. Stored per conversation in SQLite.

In production this moves to the Dixit app's database. Food preference and home foods need consent there.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SAFETY_FIELDS = ("diabetes_medicine", "bp_medicine", "pregnancy", "kidney_disease", "eating_disorder", "under_18")


@dataclass
class ChatState:
    conversation_id: str
    turn: int = 0
    protocol_explained_turn: int | None = None
    protocol_reminded_turn: int | None = None
    safety_status: dict = field(default_factory=lambda: {k: "unknown" for k in SAFETY_FIELDS})
    safety_asked_turn: int | None = None
    food_preference: str = "unknown"
    home_foods: list = field(default_factory=list)
    ingredients_on_hand: list = field(default_factory=list)
    conditions_mentioned: list = field(default_factory=list)
    facts_given: list = field(default_factory=list)          # "record_id:field"
    foods_discussed: list = field(default_factory=list)
    last_followup: dict | None = None                        # {"id", "slot", "turn"}
    followups_asked: list = field(default_factory=list)
    warm_opening_used: bool = False
    last_question: dict | None = None                        # type, topic, foods of the last real question (for replies)
    allergies: list | None = None                            # learnt in chat (None = unknown, [] = none); app profile wins
    allergy_asked_turn: int | None = None
    pending_confirm: dict | None = None                      # unknown pantry word -> suggested food ("did you mean ...?")
    recipe_choices: list = field(default_factory=list)       # recipe IDs offered for the person to pick
    last_subject: str = ""                                   # the food or dish the last question was about ("poha"), for "and protein?" / "what about dal?"
    last_subject_kind: str = ""                              # "nutrient" (a calories / protein question) or "allowed" (can I eat it)
    last_nutrient: str = ""                                  # calories | protein | carbs | fat | fibre, as asked
    last_subject_turn: int = -99
    last_explain: dict | None = None                         # what "why?" / "tell me more" may say about the last answer: {"kind", "lines"}
    last_explain_turn: int = -99
    last_targets: list = field(default_factory=list)         # the nutrient amounts of the last dish search [[field, value, mode], ...]
    last_targets_turn: int = -99
    target_exclude: list = field(default_factory=list)       # words the person asked to leave out of that search ("paneer")
    target_open: bool = False                                # a dish search is open (it may hold only a diet)
    target_include: list = field(default_factory=list)       # meat / fish words the dish search must contain ("chicken")
    target_diet: str = ""                                    # a narrower diet asked for within a dish search ("what about veg") for a person whose profile is wider
    target_shown: list = field(default_factory=list)         # dish codes already listed for it ("what else" skips them)
    avoid_foods: list = field(default_factory=list)          # food IDs the person said they do not eat ("i don't eat onion")
    recipes_shown: list = field(default_factory=list)        # approved recipe IDs already shown in this chat ("what else" skips them)
    drafts_shown: list = field(default_factory=list)         # names of AI-drafted dishes shown in this chat (a new draft must differ)
    diabetes_status: str = "unknown"                         # unknown | diabetic | prediabetic | non_diabetic | currently_non_diabetic
    prior_diabetes_history: bool | None = None
    consultant_validated: bool | None = None
    status_asked_turn: int | None = None
    consultant_asked_turn: int | None = None
    emergency_turn: int | None = None                        # turn of the last emergency reply
    emergency_category: str = ""
    language: str = "en"                                     # en | hi | hinglish: the language the chat is in, answers follow it
    emergency_checkin_done: bool = True                      # the one check-in line after an emergency was given
    last_route: str = ""                                     # who answered the last turn: safety | nutrition | main | clarify (router.py)
    route_pending: dict | None = None                        # a message the router asked about ("is this about food?"): {"message", "turn"}

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str, conversation_id: str | None = None) -> "ChatState":
        """Rebuild a state. Missing fields get defaults, unknown fields are ignored, and a field of the wrong type keeps its
        default (an old, hand-edited or corrupted row must never crash the chat)."""
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("chat state is not an object")
        state = cls(conversation_id=str(conversation_id or data.get("conversation_id") or ""))
        for key, value in data.items():
            if key == "conversation_id" or not hasattr(state, key):
                continue
            default = getattr(state, key)
            if default is None:                       # optional int / bool / dict fields: accept None or the matching simple type
                if value is None or isinstance(value, (int, str, dict, list, bool)):
                    setattr(state, key, value)
            elif isinstance(value, type(default)) and not (isinstance(default, int) and isinstance(value, bool) and not isinstance(default, bool)):
                setattr(state, key, value)
        if not isinstance(state.safety_status, dict):
            state.safety_status = {k: "unknown" for k in SAFETY_FIELDS}
        for key in SAFETY_FIELDS:
            if state.safety_status.get(key) not in ("yes", "no", "unknown"):
                state.safety_status[key] = "unknown"
        for key in ("last_followup", "last_question", "pending_confirm", "last_explain", "route_pending"):
            if getattr(state, key) is not None and not isinstance(getattr(state, key), dict):
                setattr(state, key, None)
        for key in ("allergies",):
            if getattr(state, key) is not None and not isinstance(getattr(state, key), list):
                setattr(state, key, None)
        for key in ("turn",):
            if not isinstance(getattr(state, key), int) or getattr(state, key) < 0:
                setattr(state, key, 0)
        return state

    def risk_level(self) -> str:
        values = [self.safety_status.get(k, "unknown") for k in SAFETY_FIELDS]
        if "yes" in values:
            return "known"
        if "unknown" in values:
            return "unknown"
        return "none"

    def risk_reasons(self) -> list[str]:
        return [k for k in SAFETY_FIELDS if self.safety_status.get(k) == "yes"]


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS chat_state (conversation_id TEXT PRIMARY KEY, state TEXT NOT NULL, "
                         "updated_at REAL NOT NULL)")
        self._db.commit()

    def load(self, conversation_id: str) -> ChatState:
        with self._lock:
            row = self._db.execute("SELECT state FROM chat_state WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if not row:
            return ChatState(conversation_id=conversation_id)
        try:
            return ChatState.from_json(row[0], conversation_id)
        except (ValueError, TypeError, KeyError, AttributeError):
            return ChatState(conversation_id=conversation_id)       # corrupted row: start the conversation fresh

    def save(self, state: ChatState) -> None:
        with self._lock:
            self._db.execute("INSERT INTO chat_state (conversation_id, state, updated_at) VALUES (?, ?, ?) "
                             "ON CONFLICT(conversation_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                             (state.conversation_id, state.to_json(), time.time()))
            self._db.commit()

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM chat_state WHERE conversation_id = ?", (conversation_id,))
            self._db.commit()


def audit_view(state: ChatState) -> dict:
    """The state a turn ended with, as separate named fields for the audit log (each field has one meaning)."""
    return {
        "emergency": {"turn": state.emergency_turn, "category": state.emergency_category or None, "checkin_done": state.emergency_checkin_done},
        "search_memory": {"open": state.target_open, "include": list(state.target_include), "exclude": list(state.target_exclude),
                          "diet": state.target_diet or None, "targets": len(state.last_targets), "turn": state.last_targets_turn},
        "risk_flags": {k: v for k, v in state.safety_status.items() if v != "unknown"},
        "diabetes": {"status": state.diabetes_status, "prior_history": state.prior_diabetes_history,
                     "consultant_validated": state.consultant_validated},
        "language": state.language,
    }
