"""The nutrition engine: the ten stages for one message, split in two for the main pipeline (`main_pipeline.py`).

`prepare` is the main pipeline's step 0: 1 receive · 2 remember (chat state) · 3 safety gate.
`respond` is the nutrition plugin (`plugin.py`): doctor pack and profile · 4 understand · 5 plan · 6 fetch + 7 compose · 8 check
(AI draft retried once, then the template answer) · 9 deliver + log + save state, returning the answer, its source badge and the
audit record. 10 learn: feedback records in the audit log. `answer` runs both, as the plugin's direct entry (tests, tools).
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import safety as safety_gate
from .binding import open_binding
from .checks import CheckResult, blocking, check_ai_text, check_final
from .cautions import FoodCautions
from .compose import Composer
from .config import Settings, load_settings
from .consistency import find_problems
from .dish_lookup import build_dish_lookup, find_all_in_text
from .lists import apply_blocked_records
from .foods import FoodIndex
from .hindi import translate as hindi_translate
from .translate_reply import ReplyTranslator, detect_language, reply_translations
from .explain import explain_lines
from .semantics import SemanticJudge
from .llm import LLMError, make_llm
from .emergency import EmergencyRules
from .harmful import HarmfulRules
from .memory import MemoryStore, apply_memory, clean_user_id, collect, findings_for
from .loader import LoadError, load_library, load_manifest, load_swastham_data
from .doctor_packs import DoctorPacks
from . import programme as programmes
from .book_recipes import convert as convert_book, load_book
from .person import AllergyFilter, apply_profile_safety, build_person, sanitize_profile
from .planner import make_plan
from .general_recipe import GeneralRecipeService
from .recipes import RecipeService, recipe_badge
from .services import Services
from .sources import answer_sources
from .status import apply_status, parse_status
from .state import ChatState, StateStore, audit_view
from .unavailable import AuditUnavailable
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
    language: str = "en"                                 # the language the answer is shown in
    remembered: list = field(default_factory=list)       # what a new chat took from memory: [{"key", "label", "at"}]
    badge: dict | None = None                            # where the answer comes from (plugin step 8): {"kind", "text", ...}
    sources: list = field(default_factory=list)          # the named datasets behind the numbers, trusted or development (sources.py)
    route: dict | None = None                            # the main pipeline's routing decision (router.py)
    handled_by: str = "plugin"                           # safety | plugin | main | router
    audit: dict = field(default_factory=dict)            # the audit record written for this turn


@dataclass
class TurnContext:
    """One message after step 0 (receive, remember, safety gate): what the router and the plugin work from."""
    conversation_id: str
    state: ChatState
    user_id: str | None
    remembered: list
    profile: dict
    original: str                                        # the message as typed
    text: str                                            # Hindi question shapes rewritten to plain words
    gate: safety_gate.SafetyResult
    started: float


@dataclass
class HandOff:
    """Main pipeline -> nutrition plugin (step 1): the message, who is asking, their doctor, the language and the router's intent."""
    message: str
    user_id: str | None = None
    doctor_id: str | None = None
    language: str = "en"
    intent: str = "direct"


class AuditLog:
    """Append-only JSON lines. Records are never edited; feedback is a new record pointing at the turn.

    If the file cannot be written, lines wait in memory and are written, in order, the next time it can (unavailable.py explains
    the policy). An emergency or harmful-intent line never makes its reply wait; other lines may wait up to `max_queued`, then
    AuditUnavailable is raised so the answer is withheld."""

    MAX_QUEUED = 50
    NEVER_WAIT = {"emergency", "harmful_intent"}

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self.max_queued = self.MAX_QUEUED
        self.failures = 0

    @property
    def queued(self) -> int:
        return len(self._queue)

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            self._queue.append(line)
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write("".join(self._queue))
                self._queue.clear()
            except OSError as exc:
                self.failures += 1
                print(f"audit log cannot be written ({exc.__class__.__name__}); {len(self._queue)} line(s) waiting", file=sys.stderr)
                if len(self._queue) > self.max_queued and record.get("type") not in self.NEVER_WAIT:
                    self._queue.pop()
                    raise AuditUnavailable("the audit log cannot be written and its waiting list is full") from exc


class Engine:
    def __init__(self, settings: Settings | None = None, llm=None):
        self.settings = settings or load_settings()
        manifest = load_manifest()
        self.library = load_library(self.settings, manifest)
        self.data = load_swastham_data(self.settings, manifest)
        self.words = self.data.word_lists
        self.emergency = EmergencyRules(self.data.emergency_rules)
        self.harmful = HarmfulRules(self.data.harmful_intent_rules)
        self.index = FoodIndex(self.data.food_master, self.words["spelling_map"])
        # Programmes' recipe books (manifest "recipe_books"): their dishes join the collection (as the programme tier) when the
        # book's recipes file is there. Each book has its own ingredient map; a doctor pack lists the books its patients get.
        self.books = {}
        for entry in self.data.manifest.get("recipe_books") or []:
            raw = load_book(self.settings.recipe_books.get(entry["id"]))
            if raw is not None:
                book = self.books[entry["id"]] = convert_book(raw, self.data.recipe_book_maps[entry["id"]], self.index.foods)
                self.data.recipes["recipes"] = self.data.recipes["recipes"] + book.recipes
        problems = find_problems(self.data, self.library, self.index, self.settings.environment)
        if problems:
            raise LoadError("data files disagree with each other:\n  - " + "\n  - ".join(problems[:20]))
        # The programme binding (manifest "programme_binding"): production refuses a missing, changed or unvalidated one.
        self.binding, self.binding_status = open_binding(self.settings, manifest)
        apply_blocked_records(self.library, self.data.black_lists, self.settings.environment)
        self.services = Services(self.library, self.data, self.index)
        self.allergy = AllergyFilter(self.data.allergen_map, self.index, self.data.black_lists)
        self.store = StateStore(self.settings.state_db)
        self.audit = AuditLog(self.settings.audit_log)
        # Past conversations and remembered facts: development only until a privacy and consent decision is made (health data, DPDP Act).
        self.memory = MemoryStore(self.settings.memory_db) if (self.settings.memory and self.settings.environment != "production") else None
        self.llm = llm or make_llm(self.settings)
        self.translator = ReplyTranslator(self.llm, self.index, self.words)
        self.judge = SemanticJudge(self.llm, self.index)          # what a still-unknown word means (only with a real model)
        ai_drafting = bool(manifest["environments"][self.settings.environment].get("recipe_ai_drafting"))
        self.recipes = RecipeService(self.library, self.data, self.index, self.allergy, self.llm, ai_drafting,
                                     self.services.golden)
        self.general = GeneralRecipeService(self.llm, self.data.general_recipe_rules, self.allergy, self.index, self.words,
                                            self.data.white_lists, self.settings.environment, self.services.golden, ai_drafting)
        self.dishes = build_dish_lookup(self.settings, self.library, self.data.dish_rules, self.words["spelling_map"])
        self.cautions = FoodCautions(self.data.food_cautions, self.words, self.index.foods)
        self.composer = Composer(self.services, self.index, self.words, self.recipes, self.allergy, self.dishes, self.general, self.cautions)
        self.packs = DoctorPacks(self.data.doctor_packs, self.data.programme_rules)
        programmes.set_fallback(self.packs.resolve().programme_obj)     # for code that runs outside a request
        self.production_sources = set(manifest["environments"]["production"]["sources"])
        self.versions = {
            "runtime": manifest["runtime_id"],
            "release": (self.data.release or {}).get("release_id"),
            "data_licence": "sources=" + "+".join(self.library.sources_on) + "; indian=" + (self.library.indian_flag or "off")
                            + "; test_only_data=" + ("allowed" if manifest["environments"][self.settings.environment].get("allow_test_only_data") else "refused"),
            "library": manifest["library"]["version"],
            "food_master": self.data.food_master["version"],
            "approved_list": self.data.approved_list["list_version"],
            "cooking_tips": self.data.cooking_tips["version"],
            "golden_rules": self.data.golden_rules["version"],
            "programme_rules": ",".join(f"{k}:{v.get('version')}" for k, v in sorted(self.data.programme_rules.items())),
            "word_lists": self.data.word_lists["version"],
            "allergen_map": self.data.allergen_map["version"],
            "recipes": self.data.recipes["collection_version"],
            "condition_limits": self.data.condition_limits["version"],
            "swap_table": self.data.swap_table["version"],
            "rules_registry": self.data.rules_registry["version"],
            "authority_ladder": self.data.authority_ladder["version"],
            "cooking_factors": self.data.cooking_factors["version"],
            "emergency_rules": self.data.emergency_rules["version"],
            "harmful_intent_rules": self.data.harmful_intent_rules["version"],
            "hinglish_glossary": self.data.hinglish_glossary["version"],
            "general_recipe_rules": self.data.general_recipe_rules["version"],
            "reply_translations": self.data.reply_translations["version"],
            "white_lists": self.data.white_lists["version"],
            "black_lists": self.data.black_lists["version"],
            "dish_rules": self.data.dish_rules["version"],
            "book_measures": self.data.book_measures["version"],
            "decision_logic": self.data.decision_logic["version"],
            "doctor_packs": self.data.doctor_packs["version"],
            "router_rules": self.data.router_rules["version"],
            "food_cautions": self.data.food_cautions["version"],
            "binding_map": self.data.binding_map["version"],
            "binding": f"{(manifest.get('programme_binding') or {}).get('id', 'none')}, {self.binding_status['state']}",
            "effective_runtime": self.binding_status["runtime_id"] or "none",
        }
        for book_id, mapping in self.data.recipe_book_maps.items():        # "recipe_book:<id>": "<map version>, <dishes> dishes" or "off"
            book = self.books.get(book_id)
            self.versions[f"recipe_book:{book_id}"] = (f"{mapping['version']}, {mapping['book'].get('short_title', book_id)}, "
                                                       f"{len(book.recipes)} dishes" if book else f"{mapping['version']}, off")

    def _dish_in_text(self, cleaned: str) -> list[dict]:
        """The dishes a message names, for understand(): [{"span", "exact"}]. exact = the words are a known dish name."""
        return [{"span": span, "exact": span in self.dishes.exact} for span, _result in find_all_in_text(self.dishes, cleaned)]

    # ------------------------------------------------------------------
    def answer(self, message: str, conversation_id: str | None = None, profile: dict | None = None, user_id: str | None = None,
               doctor_id: str | None = None) -> TurnResult:
        """Step 0 and the plugin in one call, without the router: every message is treated as nutrition related."""
        ctx = self.prepare(message, conversation_id, profile, user_id)
        if isinstance(ctx, TurnResult):
            return ctx
        return self.respond(ctx, HandOff(ctx.original, ctx.user_id, doctor_id, ctx.state.language))

    def prepare(self, message: str, conversation_id: str | None = None, profile: dict | None = None,
                user_id: str | None = None) -> TurnContext | TurnResult:
        """Main pipeline step 0: load the chat, read the language, run the emergency, harmful-intent and risk-word checks.
        Returns a TurnResult straight away for an empty or over-long message."""
        started = time.perf_counter()
        conversation_id = conversation_id or uuid.uuid4().hex
        text = (message or "").strip()
        state = self.store.load(conversation_id)
        user_id = clean_user_id(user_id) if self.memory is not None else None
        remembered: list = []
        if user_id and state.turn == 0:
            state.turn = self.memory.last_turn(conversation_id)       # state lost but history kept: carry on numbering, never overwrite
            if state.turn == 0:                            # a new chat starts from what is already known about this person
                remembered = apply_memory(state, self.memory.facts(user_id))

        if not text or len(text) > MAX_MESSAGE_CHARS:
            reply = "Please send your question in a few lines, and I'll help."
            return TurnResult(conversation_id, state.turn, reply, "invalid_message")

        # 3 safety gate (the app's profile feeds the same risk status). The profile is outside input: sanitised first.
        profile = sanitize_profile(profile)
        apply_profile_safety(profile, state)
        if profile.get("diet_type") and state.food_preference == "unknown":
            state.food_preference = profile["diet_type"]       # the app already knows: never ask it in the chat
        original = text
        rewritten = hindi_translate(text)
        state.language = detect_language(original, rewritten, state.language)
        text = rewritten                                  # Devanagari and Roman-Hindi question shapes, read as plain words
        gate_text = original if text == original else f"{original} . {text}"      # the emergency phrases include Devanagari ones
        gate = safety_gate.check(gate_text, state, self.words, self.emergency, self.harmful)
        state.safety_status.update(gate.updates)
        return TurnContext(conversation_id, state, user_id, remembered, profile, original, text, gate, started)

    def retarget(self, ctx: TurnContext, message: str) -> None:
        """Answer an earlier message instead of this one (the router asked "is this about food?" and the reply said where it goes).
        The earlier message already passed the safety gate on its own turn."""
        ctx.original = message
        ctx.text = hindi_translate(message)

    def pack_for(self, ctx: TurnContext, doctor_id: str | None):
        """The doctor pack for this patient: their doctor's, for the programme in their profile."""
        return self.packs.resolve(doctor_id, ctx.profile.get("programme"))

    def respond(self, ctx: TurnContext, handoff: HandOff | None = None) -> TurnResult:
        """The nutrition plugin, steps 2-9: doctor pack and profile, understand, plan, fetch, compose, check, log. Everything
        runs inside the pack's programme, so its names, its own lines and only its rule types apply."""
        handoff = handoff or HandOff(ctx.original, ctx.user_id, None, ctx.state.language)
        pack = self.pack_for(ctx, handoff.doctor_id)
        with programmes.use(pack.programme_obj):
            return self._respond(ctx, handoff, pack)

    def _respond(self, ctx: TurnContext, handoff: HandOff, pack) -> TurnResult:
        conversation_id, state, user_id, remembered = ctx.conversation_id, ctx.state, ctx.user_id, ctx.remembered
        profile, original, text, gate, started = ctx.profile, ctx.original, ctx.text, ctx.gate, ctx.started

        # 4 understand (+ apply what a reply tells us)
        u = understand(text, state, self.index, self.words, self.recipes.find_recipes_in_text, self.allergy.read_reply,
                       self._dish_in_text if self.dishes else None, self.judge.judge, self.composer.food_exists)
        if gate.emergency:
            u.type = "emergency"
        elif gate.harm.is_emergency:
            u.type = "harmful_intent"
        elif gate.em.glucose_mg_dl is not None and u.type in {"food_allowed", "out_of_scope", "food_choice_guidance", "neutral_nutrient_lookup", "protocol_permission"}:
            u.type, u.reading = "reading_ack", float(gate.em.glucose_mg_dl)      # "my sugar is 250": a reading, not a question about sugar
        elif u.type == "out_of_scope" and gate.updates and not u.slot_fill and "?" not in text:
            u.type = "safety_ack"                          # "i am on insulin", "i have kidney disease": thanked, noted, nothing else guessed
        if u.statement:
            st = u.statement
            if st.get("label") == "none":
                state.allergies = []                                              # "no allergies": known, and none
            if st.get("diet"):
                state.food_preference = st["diet"]                                # said outright, so it replaces an earlier guess
            if st.get("allergies"):
                state.allergies = sorted(set(state.allergies or []) | set(st["allergies"]))
            if st.get("avoid"):
                state.avoid_foods = sorted(set(state.avoid_foods) | set(st["avoid"]))
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
        person.doctor_pack = pack                          # layer 1 of the constraints: blocks and limits merge strictest first

        # 5 plan
        plan = make_plan(u, state, emergency=gate.emergency, allergies_known=person.allergies_known, status=person.status,
                         em=gate.em, harm=gate.harm)
        self._emergency_lines(plan, state, gate)

        # 6-7 fetch + compose (allergy / avoid / diet filter runs inside every service that names a food)
        draft = self.composer.build(plan, state, u, person)
        template = draft.template()
        sources = answer_sources(draft, self.library, self.index, self.dishes, self.production_sources)
        badge = self._badge(draft, plan, sources)

        # 8 check: AI reword (if any), retried once with the failed checks, then the template answer
        ai_used, fallback, ai_results = False, False, []
        final = draft.fill(template)
        if not isinstance(self.llm, type(None)) and getattr(self.llm, "name", "mock") != "mock" and plan.type not in {"emergency", "harmful_intent", "support_wellbeing"}:
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
        if any(r.level == "critical" and not r.passed for r in final_results) and plan.type not in {"emergency", "harmful_intent"}:
            # Even the template broke a critical check: nothing unchecked is shown.
            final, ai_used, fallback = SAFE_FALLBACK, False, True

        # 9 deliver + log + save state. The English answer above passed every check; a Hindi chat gets it in Hindi (translate_reply.py).
        english = final
        translation = self.translator.translate(english, state.language, plan.type, text)
        final = translation.text
        state.turn += 1
        state.last_route = "safety" if plan.type in {"emergency", "harmful_intent"} else "nutrition"
        state.route_pending = None
        if plan.type in {"emergency", "harmful_intent"}:          # both get the one check-in line on a later turn
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
        if u.type == "target_meal_search":
            state.last_targets = [[t.field, t.value, t.mode, t.high] for t in u.targets]
            state.last_targets_turn, state.target_exclude = state.turn, list(u.target_exclude)
            state.target_diet, state.target_include = u.target_diet, list(u.target_include)
            state.target_open = bool(u.targets or u.target_include or u.target_diet)
        elif u.type not in {"greeting_thanks", "out_of_scope", "explain_last"}:
            state.last_targets, state.target_include, state.target_open = [], [], False      # a different kind of question ends the dish search
        if u.type != "explain_last":
            explain = explain_lines(draft, plan, self.library)
            if explain:
                state.last_explain, state.last_explain_turn = explain, state.turn
        if u.subject:
            state.last_subject, state.last_subject_kind, state.last_subject_turn = u.subject, u.subject_kind, state.turn
            if u.subject_nutrient:
                state.last_nutrient = u.subject_nutrient
        if u.type not in {"followup_reply", "greeting_thanks", "out_of_scope", "explain_last"}:
            state.last_question = {"type": plan.type, "topic": plan.topic, "foods": plan.focus_foods,
                                   "recipe_ids": plan.recipe_ids, "wants_list": plan.wants_list,
                                   "recipe_foods": ([i["food"] for i in draft.recipe.ingredients] if draft.recipe else None)}
        self.store.save(state)

        elapsed = int((time.perf_counter() - started) * 1000)
        all_checks = [r.as_dict() for r in ai_results + final_results]
        result = TurnResult(conversation_id, state.turn, final, plan.type, plan.as_dict(), all_checks, ai_used, fallback,
                            sorted({f.record_id for f in draft.facts}), elapsed, translation.language, remembered, badge,
                            sources, handled_by="safety" if state.last_route == "safety" else "plugin")
        record = {
            "kind": "turn", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id, "turn": state.turn,
            "environment": self.settings.environment, "versions": self.versions,
            "release": {"id": (self.data.release or {}).get("release_id"), "verified": (self.data.release or {}).get("verified"),
                        "drifted": (self.data.release or {}).get("drifted", [])},
            "state": audit_view(state),
            "operational_actions": [],           # only actions the platform really completed; none exist yet, so no answer may claim one (N30)
            "type": plan.type, "plan": plan.as_dict(),
            "risk_level": state.risk_level(), "records": result.records, "locked": draft.locked, "template": template,
            "checks": all_checks, "ai": {"provider": getattr(self.llm, "name", "mock"), "model": getattr(self.llm, "model", ""),
                                         "used": ai_used, "fallback": fallback},
            "final_text": final, "ms": elapsed,
            "language": {"chat": state.language, "shown": translation.language, "method": translation.method,
                         "english_text": english if translation.method != "none" else None, "problems": translation.problems[:6]},
            "emergency": ({"category": gate.em.category, "matched": gate.em.matched, "negated": gate.em.negated,
                           "glucose_mg_dl": gate.em.glucose_mg_dl, "checkin": "emergency_checkin" in plan.blocks}
                          if (gate.em.matched or gate.em.negated or "emergency_checkin" in plan.blocks) else None),
            "harmful": ({"category": gate.harm.category, "matched": gate.harm.matched, "set_aside": gate.harm.negated}
                        if (gate.harm.matched or gate.harm.negated) else None),
            "recipe": ({"id": draft.recipe.recipe_id, "origin": draft.recipe.origin, "name": draft.recipe.name,
                        "ingredients": draft.recipe.ingredients, "totals": {k: str(v) for k, v in draft.recipe.totals.items()},
                        "gate_passed": bool(draft.recipe.gate and draft.recipe.gate.passed), "repairs": draft.recipe.repairs}
                       if draft.recipe else None),
            "recipe_trace": draft.recipe_trace,
            "general_recipe": draft.general,
            "handoff": {"intent": handoff.intent, "doctor_id": handoff.doctor_id, "language": handoff.language,
                        "user": bool(handoff.user_id)},
            "doctor_pack": pack.as_audit(),
            "badge": badge,
            "sources": [{"name": g["name"], "trusted": g["trusted"], "records": [r["id"] for r in g["records"]]} for g in sources],
        }
        self.audit.append(record)
        result.audit = record
        gaps = list(draft.gaps)
        if english == SAFE_FALLBACK:
            failed = sorted({r.check_id for r in final_results if r.level == "critical" and not r.passed})
            gaps.append({"topic": plan.type, "reason": "the answer failed a safety check: " + (", ".join(failed) or "unknown")})
        for gap in gaps[:5]:                      # the approved sources did not cover the question: logged for the dietitian
            self.audit.append({"kind": "knowledge_gap", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id,
                               "turn": state.turn, "plan_type": plan.type, "topic": str(gap["topic"])[:80], "reason": str(gap["reason"])[:120]})
        if user_id:
            self.memory.remember(user_id, collect(state), conversation_id)
            self.memory.record_turn(user_id, conversation_id, state.turn, original, final,
                                    findings_for(plan.type, plan.as_dict(), all_checks, ai_used, fallback, record))
        if draft.general and draft.general.get("shown"):
            self.audit.append({"kind": "general_recipe", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id,
                               "turn": state.turn, "request": draft.general["request"], "name": draft.general["name"],
                               "method": draft.general["method"], "ingredients": draft.general["ingredients"],
                               "steps": draft.general["steps"], "attempts": draft.general["attempts"],
                               "rules": self.versions["general_recipe_rules"], "model": getattr(self.llm, "model", ""),
                               "reviewed": False})
        for item in draft.unknown_items:
            self.audit.append({"kind": "unknown_item", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id,
                               "turn": state.turn, "word": str(item["word"])[:80], "item_kind": item["kind"],
                               "suggested_food_id": item["suggested_food_id"]})
        if draft.backlog_request:
            self.audit.append({"kind": "recipe_backlog", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "conversation_id": conversation_id, "turn": state.turn, "message": original[:200],
                               "ingredients": plan.ingredients, "recipe_ids": plan.recipe_ids})
        return result

    @staticmethod
    def _badge(draft, plan, sources: list) -> dict | None:
        """Plugin step 8: the source of the answer, shown beside it. Only answers with a clear single source get one."""
        if plan.type in {"emergency", "harmful_intent"}:
            return None
        if draft.recipe is not None:
            return recipe_badge(draft.recipe)
        if draft.general and draft.general.get("shown"):
            return {"kind": "ai_general", "text": "AI-drafted general recipe (not nutrition-checked)"}
        if draft.facts and sources:
            named = [g for g in sources if "nutrition values" in g["used_for"]] or sources
            trusted = all(g["trusted"] for g in named)
            text = "Nutrition values: " + " and ".join(dict.fromkeys(g["short"] for g in named))
            return {"kind": "nutrition_table" if trusted else "nutrition_table_dev", "text": text, "trusted": trusted}
        return None

    def finish_main(self, ctx: TurnContext, english: str, kind: str, route: dict, handled_by: str, extra: dict | None = None) -> TurnResult:
        """Deliver a turn the plugin did not answer (the main assistant, or the router's question): translate, save, log."""
        state = ctx.state
        translation = self.translator.translate(english, state.language, kind, ctx.text)
        state.turn += 1
        state.last_route = "clarify" if kind == "clarify" else "main"
        state.route_pending = {"message": ctx.original[:MAX_MESSAGE_CHARS], "turn": state.turn} if kind == "clarify" else None
        state.last_followup = None
        self.store.save(state)
        elapsed = int((time.perf_counter() - ctx.started) * 1000)
        record = {"kind": "turn", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": ctx.conversation_id, "turn": state.turn,
                  "environment": self.settings.environment, "versions": self.versions, "type": kind, "route": route,
                  "handled_by": handled_by, "risk_level": state.risk_level(), "final_text": translation.text, "ms": elapsed,
                  "ai": {"provider": getattr(self.llm, "name", "mock"), "model": getattr(self.llm, "model", "")},
                  "language": {"chat": state.language, "shown": translation.language, "method": translation.method,
                               "english_text": english if translation.method != "none" else None},
                  **(extra or {})}
        self.audit.append(record)
        if ctx.user_id:
            self.memory.remember(ctx.user_id, collect(state), ctx.conversation_id)
            self.memory.record_turn(ctx.user_id, ctx.conversation_id, state.turn, ctx.original, translation.text,
                                    findings_for(kind, {}, [], (extra or {}).get("method") == "model", False, record))
        return TurnResult(ctx.conversation_id, state.turn, translation.text, kind, timing_ms=elapsed, language=translation.language,
                          remembered=ctx.remembered, route=route, handled_by=handled_by, audit=record)

    def feedback(self, conversation_id: str, turn: int, kind: str, note: str = "") -> None:
        allowed = {"helpful", "not_what_i_asked", "cant_get_this", "something_wrong"}
        if kind not in allowed:
            raise ValueError(f"feedback must be one of {sorted(allowed)}")
        self.audit.append({"kind": "feedback", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "conversation_id": conversation_id,
                           "turn": turn, "feedback": kind, "note": note[:500]})
        if self.memory is not None:
            self.memory.record_feedback(conversation_id, turn, kind)

    def reset(self, conversation_id: str) -> None:
        self.store.delete(conversation_id)

    # ------------------------------------------------------------------
    def _emergency_lines(self, plan, state, gate) -> None:
        """After an emergency reply, the next normal answer starts with one check-in line (once, within 3 turns). A symptom
        mentioned with a negation ("no chest pain") gets one short line instead of an emergency stop."""
        if plan.type in {"emergency", "harmful_intent"}:
            return
        if not state.emergency_checkin_done and state.emergency_turn is not None and state.turn + 1 - state.emergency_turn <= 3:
            cat = self.emergency.by_id.get(state.emergency_category) or self.harmful.by_id.get(state.emergency_category)
            if cat and cat.get("checkin"):
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
