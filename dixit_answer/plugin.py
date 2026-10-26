"""The nutrition plugin: what the main pipeline hands a nutrition-related message to (steps 1-9).

1 receive the hand-off (message, user ID, doctor ID, language, intent) · 2 load the doctor pack for the patient's programme and the
profile · 3 map names to food IDs (unknown words go to the review queue; a close match is confirmed first) · 4 merge the constraint
layers strictest first (doctor pack, patient safety, preferences) · 5 search recipes: doctor-approved, dietitian-signed, reference
· 6 a found and fits, b changed by code (swap or scale), c not found (the model drafts food IDs, grams and steps) · 7 compute the
numbers from the food table and run the policy gate (repair, then at most 2 model retries, then a safe fallback) · 8 render the
answer with its source badge · 9 return the answer, badge and audit record; the main pipeline sends the reply.

The steps themselves live in the engine (`pipeline.Engine.respond`, `recipes.py`, `doctor_packs.py`); this module is the boundary,
so that the Dixit app can later call the plugin the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pipeline import Engine, HandOff, TurnContext, TurnResult


@dataclass
class PluginResult:
    answer: str
    badge: dict | None
    audit: dict = field(default_factory=dict)
    turn: TurnResult | None = None                   # the full result, for the main pipeline's reply


class NutritionPlugin:
    def __init__(self, engine: Engine):
        self.engine = engine

    def handle(self, handoff: HandOff, ctx: TurnContext) -> PluginResult:
        result = self.engine.respond(ctx, handoff)
        return PluginResult(result.answer, result.badge, result.audit, result)
