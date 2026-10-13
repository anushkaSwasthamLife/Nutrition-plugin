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
    diabetes_status: str = "unknown"                         # unknown | diabetic | prediabetic | non_diabetic | currently_non_diabetic
    prior_diabetes_history: bool | None = None
    consultant_validated: bool | None = None
    status_asked_turn: int | None = None
    consultant_asked_turn: int | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "ChatState":
        data = json.loads(text)
        state = cls(conversation_id=data["conversation_id"])
        for key, value in data.items():
            if hasattr(state, key):
                setattr(state, key, value)
        for key in SAFETY_FIELDS:
            state.safety_status.setdefault(key, "unknown")
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
        return ChatState.from_json(row[0]) if row else ChatState(conversation_id=conversation_id)

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
