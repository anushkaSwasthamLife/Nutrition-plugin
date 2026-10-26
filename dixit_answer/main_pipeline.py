"""The main pipeline: every chat message enters here.

0 the message arrives; the emergency and safety checks run first (an emergency or harmful-intent reply ends the turn) ·
R the intent router asks whether the message is nutrition related: if so, it is handed to the nutrition plugin (`plugin.py`);
if not, the main assistant answers (`main_ai.py`); if unsure, one question is asked and the reply decides ·
9 the plugin returns the answer, its source badge and the audit record, and the main pipeline sends the reply.
"""
from __future__ import annotations

from .main_ai import MainAssistant
from .pipeline import Engine, HandOff, TurnResult
from .plugin import NutritionPlugin
from .router import MAIN, NUTRITION, IntentRouter, Route


class MainPipeline:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or Engine()
        eng = self.engine
        dish_finder = (lambda cleaned: eng._dish_in_text(cleaned)) if eng.dishes else None
        self.router = IntentRouter(eng.data.router_rules, eng.index, eng.words, eng.recipes.alias_to_recipe.keys(), dish_finder)
        self.plugin = NutritionPlugin(eng)
        self.assistant = MainAssistant(eng.llm, eng.data.router_rules)

    def answer(self, message: str, conversation_id: str | None = None, profile: dict | None = None, user_id: str | None = None,
               doctor_id: str | None = None) -> TurnResult:
        ctx = self.engine.prepare(message, conversation_id, profile, user_id)          # step 0
        if isinstance(ctx, TurnResult):
            ctx.handled_by = "main"
            return ctx
        if ctx.gate.emergency or ctx.gate.harm.is_emergency:
            # The fixed safety reply is built by the engine's emergency path; it never reaches the router or the AI.
            result = self.engine.respond(ctx, HandOff(ctx.original, ctx.user_id, doctor_id, ctx.state.language, "safety"))
            result.route, result.handled_by = {"kind": "safety", "intent": result.type, "resolved_earlier_message": False}, "safety"
            return result

        route = self.router.route(ctx.text, ctx.state)                                  # step R
        if route.message:
            self.engine.retarget(ctx, route.message)
        if route.kind == NUTRITION:
            handoff = HandOff(ctx.original, ctx.user_id, doctor_id, ctx.state.language, route.intent)   # step 1
            out = self.plugin.handle(handoff, ctx)
            out.turn.route = route.as_dict()
            out.audit["route"] = route.as_dict()
            return out.turn                                                              # step 9: the main pipeline sends it
        if route.kind == MAIN:
            return self._main(ctx, route)
        return self.engine.finish_main(ctx, self.router.rules["clarify_question"], "clarify", route.as_dict(), "router")

    def _main(self, ctx, route: Route) -> TurnResult:
        topic = "general" if route.intent == "clarified" else route.intent
        reply = self.assistant.reply(ctx.original, topic, noted=bool(ctx.gate.updates))
        extra = {"main_ai": {"topic": topic, "method": reply.method, "problems": reply.problems[:4]}, "method": reply.method}
        return self.engine.finish_main(ctx, reply.text, "main_ai", route.as_dict(), "main", extra)
