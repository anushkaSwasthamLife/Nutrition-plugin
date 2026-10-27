"""The programme a patient follows, as the doctor pack defines it: the only layer that differs between doctors.

Everything else in the engine is shared: safety, the food table and allergens, USDA maths, the condition limits, the router and
the main assistant, recipes, the gate and the checks. They ask this module three things:

- **identity**: how to name the programme, the assistant and the person to ask ("the Dixit programme", "your Dixit coach").
  Shared wording is written with placeholders ({programme}, {coach}...) and filled from it.
- **texts**: lines a programme words its own way ("The Dixit programme doesn't ban foods..."); a programme without its own line
  gets the neutral default here.
- **rules**: the programme's rules engine (`golden.py`), with only the rule types the pack switches on.

The active programme is per request (a context variable), so two patients of different doctors can chat at the same time.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .golden import GoldenRules

# Every rule type the engine knows. A pack lists the ones its programme uses; the others behave neutrally.
RULE_TYPES = ("meal_order", "food_positions", "status_food_rule", "between_meals", "home_meal_kinds", "no_sweets_in_recipes",
              "forbidden_claims")

NEUTRAL_IDENTITY = {
    "name": "your programme",                       # a title: "Dixit programme"
    "programme": "your programme",                  # in a sentence: "the Dixit programme"
    "assistant": "a nutrition assistant",           # "the Dixit nutrition assistant"
    "assistant_title": "Nutrition Assistant",       # the chat page's title
    "coach": "your doctor",                         # who to ask: "your Dixit coach"
    "coach_bare": "dietitian",                      # after "your doctor or ...": "Dixit coach"
    "app": "a food and nutrition app",              # for the language model: "the Dixit Lifestyle app, a food and meals ..."
    "meals_heading": "How your meals work",
}

NEUTRAL_TEXTS = {
    "choice_intro": "Some foods are worth choosing more often.",
    "explain_intro": "Here's how {programme} works:",
    "no_meal_rules": "Your doctor hasn't set meal rules for {programme} here, so I can't explain them. Please ask {coach}.",
    "between_meals_none": "Your doctor or dietitian decides whether you eat between meals.",
    "position_none": "{Food} can be part of your meals; please ask {coach} how it fits your plan.",
    "draft_meal_place": "a home-cooked main dish; a sabji or dal",
    "portion": "I can't give amounts to eat or drink. Please ask {coach} if you want to talk about amounts.",
    "plan_refusal": "I can't write a meal plan or diet chart. I can suggest dishes for a meal if you tell me what you have.",
    "frequency_note": "",
}


@dataclass(frozen=True)
class Programme:
    id: str
    identity: dict = field(default_factory=lambda: dict(NEUTRAL_IDENTITY))
    texts: dict = field(default_factory=dict)
    rules: GoldenRules = field(default_factory=lambda: GoldenRules.empty())

    def fill(self, text: str) -> str:
        """Put the identity into shared wording. Plain replacement, so JSON braces in prompts are left alone."""
        for key, value in self.identity.items():
            text = text.replace("{" + key + "}", value)
            text = text.replace("{" + key[:1].upper() + key[1:] + "}", value[:1].upper() + value[1:])
        return text

    def text(self, key: str, **values) -> str:
        line = self.fill(self.texts.get(key) or NEUTRAL_TEXTS[key])
        for k, v in values.items():
            line = line.replace("{" + k + "}", v).replace("{" + k[:1].upper() + k[1:] + "}", v[:1].upper() + v[1:])
        return line

    def uses(self, rule_type: str) -> bool:
        return self.rules.uses(rule_type)


NEUTRAL = Programme("neutral")
_current: ContextVar[Programme | None] = ContextVar("programme", default=None)
_fallback: list[Programme] = [NEUTRAL]          # the default doctor's programme, for code that runs outside a request


def current() -> Programme:
    return _current.get() or _fallback[0]


def set_fallback(programme: Programme) -> None:
    """Set once at start-up (Engine.__init__), never per request: the list is shared by every request. A request names its
    programme with `use`, which is per request."""
    _fallback[0] = programme


@contextmanager
def use(programme: Programme):
    token = _current.set(programme)
    try:
        yield programme
    finally:
        _current.reset(token)


def build(pid: str, prog: dict, rule_data: dict) -> Programme:
    """A programme from its doctor-pack entry and the rules files the manifest loaded ({key: data})."""
    rules_cfg = prog.get("rules") or {}
    data = rule_data.get(rules_cfg.get("data")) if rules_cfg.get("data") else None
    types = rules_cfg.get("rule_types")
    rules = GoldenRules(data, set(types) if types is not None else None) if data else GoldenRules.empty()
    identity = {**NEUTRAL_IDENTITY, **(prog.get("identity") or {})}
    return Programme(pid, identity, dict(prog.get("texts") or {}), rules)
