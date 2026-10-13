"""The answer pipeline: the ten stages for one message.

1 receive · 2 remember (chat state) · 3 safety gate · 4 understand · 5 plan · 6 fetch + 7 compose · 8 check (AI draft retried
once, then the template answer) · 9 deliver + log + save state · 10 learn (feedback records in the audit log).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import safety as safety_gate
from .checks import CheckResult, blocking, check_ai_text, check_final
from .compose import Composer
from .config import Settings, load_settings
from .foods import FoodIndex
from .llm import LLMError, make_llm
from .loader import load_library, load_manifest, load_swastham_data
from .planner import make_plan
from .services import Services
from .state import StateStore
from .understand import understand

MAX_MESSAGE_CHARS = 1000


@dataclass
class TurnResult:
    conversation_id: str
    turn: int
    answer: str
    type: str
    plan: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    ai_used: bool = False
    fallback: bool = False
    records: list = field(default_factory=list)
    timing_ms: int = 0


class AuditLog:
    """Append-only JSON lines. Records are never edited; feedback is a new record pointing at the turn."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class Engine:
    def __init__(self, settings: Settings | None = None, llm=None):
        self.settings = settings or load_settings()
        manifest = load_manifest()
        self.library = load_library(self.settings, manifest)
        self.data = load_swastham_data(self.settings, manifest)
        self.words = self.data.word_lists
        self.index = FoodIndex(self.data.food_master, self.words["spelling_map"])
        self.services = Services(self.library, self.data, self.index)
        self.composer = Composer(self.services, self.index, self.words)
        self.store = StateStore(self.settings.state_db)
        self.audit = AuditLog(self.settings.audit_log)
        self.llm = llm or make_llm(self.settings)
        self.versions = {
            "runtime": manifest["runtime_id"],
            "library": manifest["library"]["version"],
            "food_master": self.data.food_master["version"],
            "approved_list": self.data.approved_list["list_version"],
            "cooking_tips": self.data.cooking_tips["version"],
            "golden_rules": self.data.golden_rules["version"],
            "word_lists": self.data.word_lists["version"],
        }

    # ------------------------------------------------------------------
    def answer(self, message: str, conversation_id: str | None = None) -> TurnResult:
        started = time.perf_counter()
        conversation_id = conversation_id or uuid.uuid4().hex
        text = (message or "").strip()
        state = self.store.load(conversation_id)

        if not text or len(text) > MAX_MESSAGE_CHARS:
            reply = "Please send your question in a few lines, and I'll help."
            return TurnResult(conversation_id, state.turn, reply, "invalid_message")

        # 3 safety gate
        gate = safety_gate.check(text, state, self.words)
        state.safety_status.update(gate.updates)

        # 4 understand (+ apply what a reply tells us)
        u = understand(text, state, self.index, self.words)
        if gate.emergency:
            u.type = "emergency"
        self._apply_slot_fill(u, state, text)
        for condition in u.conditions:
            if condition not in state.conditions_mentioned:
                state.conditions_mentioned.append(condition)
        if not u.conditions and state.conditions_mentioned:
            u.conditions = list(state.conditions_mentioned)
        if u.preference and state.food_preference == "unknown":
            state.food_preference = u.preference
        if u.ingredients:
            state.ingredients_on_hand = list(u.ingredients)

        # 5 plan
        plan = make_plan(u, state, emergency=gate.emergency)

        # 6-7 fetch + compose
        draft = self.composer.build(plan, state, u)
        template = draft.template()

        # 8 check: AI reword (if any), retried once with the failed checks, then the template answer
        ai_used, fallback, ai_results = False, False, []
        final = draft.fill(template)
        if not isinstance(self.llm, type(None)) and getattr(self.llm, "name", "mock") != "mock" and plan.type != "emergency":
            failed_ids: list = []
            for attempt in range(2):
                try:
                    ai_text = self.llm.reword(template, text, plan.max_words, failed_ids or None)
                except LLMError as exc:
                    ai_results = [CheckResult("LLM", False, "major", str(exc))]
                    break
                ai_results = check_ai_text(ai_text, draft, plan, self.index, self.words)
                bad = blocking(ai_results)
                if not bad:
                    final, ai_used = draft.fill(ai_text), True
                    break
                failed_ids = [f"{r.check_id}: {r.detail}" for r in bad]
            fallback = not ai_used
        final_results = check_final(final, draft, plan, state, self.words)
        if blocking(final_results) and ai_used:
            final, ai_used, fallback = draft.fill(template), False, True
            final_results = check_final(final, draft, plan, state, self.words)

        # 9 deliver + log + save state
        state.turn += 1
        if plan.protocol_level == "full":
            state.protocol_explained_turn = state.turn
        elif plan.protocol_level in {"reminder", "meal_place"}:
            state.protocol_reminded_turn = state.turn
        if plan.warm_opening:
            state.warm_opening_used = True
        for fact in draft.facts:
            key = f"{fact.record_id}:{fact.field}"
            if key not in state.facts_given:
                state.facts_given.append(key)
        for fid in plan.focus_foods:
            if fid not in state.foods_discussed:
                state.foods_discussed.append(fid)
        if plan.followup:
            state.last_followup = {**plan.followup, "turn": state.turn}
            state.followups_asked.append(plan.followup["id"])
            if plan.followup["id"] == "FU-SAFETY":
                state.safety_asked_turn = state.turn
        else:
            state.last_followup = None
        if u.type not in {"followup_reply", "greeting_thanks", "out_of_scope"}:
            state.last_question = {"type": plan.type, "topic": plan.topic, "foods": plan.focus_foods}
        self.store.save(state)

        elapsed = int((time.perf_counter() - started) * 1000)
        all_checks = [r.as_dict() for r in ai_results + final_results]
        result = TurnResult(conversation_id, state.turn, final, plan.type, plan.as_dict(), all_checks, ai_used, fallback,
                            sorted({f.record_id for f in draft.facts}), elapsed)
        self.audit.append({
            "kind": "turn", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id, "turn": state.turn,
            "environment": self.settings.environment, "versions": self.versions, "type": plan.type, "plan": plan.as_dict(),
            "risk_level": state.risk_level(), "records": result.records, "locked": draft.locked, "template": template,
            "checks": all_checks, "ai": {"provider": getattr(self.llm, "name", "mock"), "model": getattr(self.llm, "model", ""),
                                         "used": ai_used, "fallback": fallback},
            "final_text": final, "ms": elapsed,
        })
        return result

    def feedback(self, conversation_id: str, turn: int, kind: str, note: str = "") -> None:
        allowed = {"helpful", "not_what_i_asked", "cant_get_this", "something_wrong"}
        if kind not in allowed:
            raise ValueError(f"feedback must be one of {sorted(allowed)}")
        self.audit.append({"kind": "feedback", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id,
                           "turn": turn, "feedback": kind, "note": note[:500]})

    def reset(self, conversation_id: str) -> None:
        self.store.delete(conversation_id)

    # ------------------------------------------------------------------
    def _apply_slot_fill(self, u, state, text: str) -> None:
        if not u.slot_fill:
            return
        if "safety" in u.slot_fill:
            state.safety_status.update(safety_gate.apply_safety_reply(text, state, self.words))
        if "home_foods" in u.slot_fill:
            for fid in u.slot_fill["home_foods"]:
                if fid not in state.home_foods:
                    state.home_foods.append(fid)
        if "food_preference" in u.slot_fill:
            state.food_preference = u.slot_fill["food_preference"]
        if "ingredients" in u.slot_fill:
            state.ingredients_on_hand = list(u.slot_fill["ingredients"])
