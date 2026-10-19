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
from .consistency import find_problems
from .dish_lookup import build_dish_lookup, find_all_in_text
from .lists import apply_blocked_records
from .foods import FoodIndex
from .llm import LLMError, make_llm
from .emergency import EmergencyRules
from .loader import LoadError, load_library, load_manifest, load_swastham_data
from .person import AllergyFilter, apply_profile_safety, build_person, sanitize_profile
from .planner import make_plan
from .recipes import RecipeService
from .services import Services
from .status import apply_status, parse_status
from .state import StateStore
from .understand import understand

MAX_MESSAGE_CHARS = 1000
SAFE_FALLBACK = ("I can't give a reliable answer to that right now. Your dietitian can help, and I've noted your question "
                 "for our team.")


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
        self.emergency = EmergencyRules(self.data.emergency_rules)
        self.index = FoodIndex(self.data.food_master, self.words["spelling_map"])
        problems = find_problems(self.data, self.library, self.index, self.settings.environment)
        if problems:
            raise LoadError("data files disagree with each other:\n  - " + "\n  - ".join(problems[:20]))
        apply_blocked_records(self.library, self.data.black_lists, self.settings.environment)
        self.services = Services(self.library, self.data, self.index)
        self.allergy = AllergyFilter(self.data.allergen_map, self.index, self.data.black_lists)
        self.store = StateStore(self.settings.state_db)
        self.audit = AuditLog(self.settings.audit_log)
        self.llm = llm or make_llm(self.settings)
        ai_drafting = bool(manifest["environments"][self.settings.environment].get("recipe_ai_drafting"))
        self.recipes = RecipeService(self.library, self.data, self.index, self.allergy, self.llm, ai_drafting,
                                     self.services.golden)
        self.dishes = build_dish_lookup(self.settings, self.library, self.data.dish_rules, self.words["spelling_map"])
        self.composer = Composer(self.services, self.index, self.words, self.recipes, self.allergy, self.dishes)
        self.versions = {
            "runtime": manifest["runtime_id"],
            "library": manifest["library"]["version"],
            "food_master": self.data.food_master["version"],
            "approved_list": self.data.approved_list["list_version"],
            "cooking_tips": self.data.cooking_tips["version"],
            "golden_rules": self.data.golden_rules["version"],
            "word_lists": self.data.word_lists["version"],
            "allergen_map": self.data.allergen_map["version"],
            "recipes": self.data.recipes["collection_version"],
            "condition_limits": self.data.condition_limits["version"],
            "swap_table": self.data.swap_table["version"],
            "rules_registry": self.data.rules_registry["version"],
            "cooking_factors": self.data.cooking_factors["version"],
            "emergency_rules": self.data.emergency_rules["version"],
            "white_lists": self.data.white_lists["version"],
            "black_lists": self.data.black_lists["version"],
            "dish_rules": self.data.dish_rules["version"],
        }

    def _dish_in_text(self, cleaned: str) -> list[dict]:
        """The dishes a message names, for understand(): [{"span", "exact"}]. exact = the words are a known dish name."""
        return [{"span": span, "exact": span in self.dishes.exact} for span, _result in find_all_in_text(self.dishes, cleaned)]

    # ------------------------------------------------------------------
    def answer(self, message: str, conversation_id: str | None = None, profile: dict | None = None) -> TurnResult:
        started = time.perf_counter()
        conversation_id = conversation_id or uuid.uuid4().hex
        text = (message or "").strip()
        state = self.store.load(conversation_id)

        if not text or len(text) > MAX_MESSAGE_CHARS:
            reply = "Please send your question in a few lines, and I'll help."
            return TurnResult(conversation_id, state.turn, reply, "invalid_message")

        # 3 safety gate (the app's profile feeds the same risk status). The profile is outside input: sanitised first.
        profile = sanitize_profile(profile)
        apply_profile_safety(profile, state)
        if profile.get("diet_type") and state.food_preference == "unknown":
            state.food_preference = profile["diet_type"]       # the app already knows: never ask it in the chat
        gate = safety_gate.check(text, state, self.words, self.emergency)
        state.safety_status.update(gate.updates)

        # 4 understand (+ apply what a reply tells us)
        u = understand(text, state, self.index, self.words, self.recipes.find_recipes_in_text, self.allergy.read_reply,
                       self._dish_in_text if self.dishes else None)
        if gate.emergency:
            u.type = "emergency"
        self._apply_slot_fill(u, state, text)
        for condition in u.conditions:
            if condition not in state.conditions_mentioned:
                state.conditions_mentioned.append(condition)
        if not u.conditions and state.conditions_mentioned:
            u.conditions = list(state.conditions_mentioned)
        if not u.conditions:
            u.conditions = [c for c in profile.get("conditions", []) if c in self.words["conditions"]]      # the app already knows
        if u.preference and state.food_preference == "unknown":
            state.food_preference = u.preference
        if u.ingredients:
            state.ingredients_on_hand = list(u.ingredients)
        shown = (state.last_question or {}).get("recipe_foods")
        if "allergies" in u.slot_fill and shown is not None:
            u.ack_only = not (self.allergy.foods_for(state.allergies or []) & self.index.with_generics(shown))
        if "allerg" in u.cleaned and "allergies" not in u.slot_fill:
            mentioned = self.allergy.read_reply(u.cleaned)
            if mentioned is not None:
                state.allergies = sorted(set((state.allergies or []) + mentioned))
        if not u.slot_fill:
            apply_status(state, parse_status(text))          # "I have diabetes", "my diabetes is now normal" ...
        person = build_person(profile, state, self.index, self.allergy)

        # 5 plan
        plan = make_plan(u, state, emergency=gate.emergency, allergies_known=person.allergies_known, status=person.status,
                         em=gate.em)
        self._emergency_lines(plan, state, gate)

        # 6-7 fetch + compose (allergy / avoid / diet filter runs inside every service that names a food)
        draft = self.composer.build(plan, state, u, person)
        template = draft.template()

        # 8 check: AI reword (if any), retried once with the failed checks, then the template answer
        ai_used, fallback, ai_results = False, False, []
        final = draft.fill(template)
        if not isinstance(self.llm, type(None)) and getattr(self.llm, "name", "mock") != "mock" and plan.type not in {"emergency", "support_wellbeing"}:
            failed_ids: list = []
            for attempt in range(2):
                try:
                    ai_text = self.llm.reword(template, text, plan.max_words, failed_ids or None)
                except Exception as exc:                  # noqa: BLE001 - whatever the provider does, the template answer is the fallback
                    ai_results = [CheckResult("LLM", False, "major", f"{exc.__class__.__name__}: {str(exc)[:80]}")]
                    break
                if not isinstance(ai_text, str):
                    ai_results = [CheckResult("LLM", False, "major", "wording was not text")]
                    break
                ai_results = check_ai_text(ai_text, draft, plan, self.index, self.words, self.services.golden)
                bad = blocking(ai_results)
                if not bad:
                    final, ai_used = draft.fill(ai_text), True
                    break
                failed_ids = [f"{r.check_id}: {r.detail}" for r in bad]
            fallback = not ai_used
        final_results = check_final(final, draft, plan, state, self.words, self.index, self.services.golden)
        if blocking(final_results) and ai_used:
            final, ai_used, fallback = draft.fill(template), False, True
            final_results = check_final(final, draft, plan, state, self.words, self.index, self.services.golden)
        if any(r.level == "critical" and not r.passed for r in final_results) and plan.type != "emergency":
            # Even the template broke a critical check: nothing unchecked is shown.
            final, ai_used, fallback = SAFE_FALLBACK, False, True

        # 9 deliver + log + save state
        state.turn += 1
        if plan.type == "emergency":
            state.emergency_turn, state.emergency_category = state.turn, plan.emergency_category
            state.emergency_checkin_done = False
        elif "emergency_checkin" in plan.blocks:
            state.emergency_checkin_done = True
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
            if plan.followup["id"] == "FU-ALLERGY":
                state.allergy_asked_turn = state.turn
            if plan.followup["id"] == "FU-STATUS":
                state.status_asked_turn = state.turn
            if plan.followup["id"] == "FU-CONSULTANT":
                state.consultant_asked_turn = state.turn
        else:
            state.last_followup = None
        if u.type not in {"followup_reply", "greeting_thanks", "out_of_scope"}:
            state.last_question = {"type": plan.type, "topic": plan.topic, "foods": plan.focus_foods,
                                   "recipe_ids": plan.recipe_ids, "wants_list": plan.wants_list,
                                   "recipe_foods": ([i["food"] for i in draft.recipe.ingredients] if draft.recipe else None)}
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
            "emergency": ({"category": gate.em.category, "matched": gate.em.matched, "negated": gate.em.negated,
                           "glucose_mg_dl": gate.em.glucose_mg_dl, "checkin": "emergency_checkin" in plan.blocks}
                          if (gate.em.matched or gate.em.negated or "emergency_checkin" in plan.blocks) else None),
            "recipe": ({"id": draft.recipe.recipe_id, "origin": draft.recipe.origin, "name": draft.recipe.name,
                        "ingredients": draft.recipe.ingredients, "totals": {k: str(v) for k, v in draft.recipe.totals.items()},
                        "gate_passed": bool(draft.recipe.gate and draft.recipe.gate.passed), "repairs": draft.recipe.repairs}
                       if draft.recipe else None),
            "recipe_trace": draft.recipe_trace,
        })
        for item in draft.unknown_items:
            self.audit.append({"kind": "unknown_item", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id,
                               "turn": state.turn, "word": str(item["word"])[:80], "item_kind": item["kind"],
                               "suggested_food_id": item["suggested_food_id"]})
        if draft.backlog_request:
            self.audit.append({"kind": "recipe_backlog", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "conversation_id": conversation_id, "turn": state.turn, "message": text[:200],
                               "ingredients": plan.ingredients, "recipe_ids": plan.recipe_ids})
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
    def _emergency_lines(self, plan, state, gate) -> None:
        """After an emergency reply, the next normal answer starts with one check-in line (once, within 3 turns). A symptom
        mentioned with a negation ("no chest pain") gets one short line instead of an emergency stop."""
        if plan.type == "emergency":
            return
        if not state.emergency_checkin_done and state.emergency_turn is not None and state.turn + 1 - state.emergency_turn <= 3:
            cat = self.emergency.by_id.get(state.emergency_category)
            if cat:
                plan.blocks.insert(0, "emergency_checkin")
                plan.emergency_lines["emergency_checkin"] = cat["checkin"]
                plan.rules.append("R1-CHECKIN")
                return
        if gate.em.negated:
            plan.blocks.append("emergency_note")
            plan.emergency_lines["emergency_note"] = self.emergency.negated_note
            plan.rules.append("R1-NOTE")

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
        if "allergies" in u.slot_fill:
            state.allergies = list(u.slot_fill["allergies"])
        if "diabetes_status" in u.slot_fill:
            apply_status(state, u.slot_fill["diabetes_status"])
        if "consultant" in u.slot_fill:
            state.consultant_validated = bool(u.slot_fill["consultant"])
        if "confirm_food" in u.slot_fill:
            answer, pending = u.slot_fill["confirm_food"], state.pending_confirm
            food = pending["food_id"] if (answer is True and pending) else (answer if isinstance(answer, str) else None)
            if food and food not in state.ingredients_on_hand:
                state.ingredients_on_hand.append(food)
            state.pending_confirm = None
