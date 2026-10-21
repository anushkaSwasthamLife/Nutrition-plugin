"""What "why?" and "tell me more" may say about the last answer (stage 7b).

Only things already written down are used: the approved reason a dish fits, and the source of each number. Nothing new is claimed.
Where there is no written reason (a rule of the programme, for example) the chat says so and points to the coach.
"""
from __future__ import annotations

NO_REASON = "I don't have a written reason for that yet. For the thinking behind the programme's rules, please ask your Dixit coach."


def readable_source(source: str) -> str:
    if source == "USDA":
        return "USDA FoodData Central"
    if source.startswith("calculated"):
        return "calculated from the dish's ingredients using USDA values"
    return "the Swastham Indian meal library (development data, not yet signed off by a dietitian)"


def explain_lines(draft, plan, library) -> dict | None:
    """What the last answer can be explained with, or None when it was not an answer worth explaining."""
    recipe = getattr(draft, "recipe", None)
    if recipe is not None and getattr(recipe, "origin", "") == "approved":
        lines = [recipe.why_it_fits] if recipe.why_it_fits else []
        lines.append("Every dish I show passes the checks for your allergies, your diet and the limits for a meal before it reaches you.")
        lines.append("The nutrition shown for a dish is calculated from the USDA values of its ingredients.")
        return {"kind": "recipe", "lines": lines}
    facts = getattr(draft, "facts", None) or []
    programme = plan.type in {"food_allowed", "protocol_permission", "personal_suitability", "food_choice_guidance", "meal_protocol_application",
                              "protocol_explain", "status_dependent_protocol_question", "support_struggle"}
    if facts:
        seen, lines = set(), ([NO_REASON] if programme else [])
        for fact in facts:
            key = (fact.food_name, fact.source)
            if key in seen or len(lines) >= 3:
                continue
            seen.add(key)
            name = fact.food_name.split(" (")[0]
            lines.append(f"The figure for {name} comes from {readable_source(fact.source)}, {fact.serving}.")
        return {"kind": "nutrient", "lines": lines}
    if programme:
        return {"kind": "programme", "lines": [NO_REASON]}
    return None
