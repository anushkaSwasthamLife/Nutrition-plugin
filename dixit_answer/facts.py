"""Nutrition facts as locked items (stage 6b).

A fact is built only from values present in a record (N1, NUT-PRECISION-001): no gram guesses, approximations labelled,
energy shown as a range when the record's usage constraint says so (N12, N13), and nothing from records a quality gate blocked.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .loader import FLAG_ENERGY_MISSING, FLAG_MACRO_MISMATCH, FLAG_SERVING_UNCLEAR, Library

FIELD_LABELS = {
    "energy_kcal": ("kcal", ""),
    "carbohydrate_g": ("g", "carbohydrate"),
    "fiber_g": ("g", "fibre"),
    "protein_g": ("g", "protein"),
    "fat_g": ("g", "fat"),
}


@dataclass
class Fact:
    record_id: str
    field: str
    value: float | None
    text: str                 # e.g. "about 109 kcal per bowl"
    serving: str              # e.g. "per bowl", "per 100 g"
    source: str               # "USDA" or the Indian development flag
    food_name: str

    def as_dict(self) -> dict:
        return asdict(self)


def _num(value: float, unit: str) -> str:
    if unit == "kcal":
        return f"{round(value):d}"
    rounded = round(value, 1)
    return f"{rounded:g}"


def _serving_text(record: dict) -> str | None:
    serving = record.get("serving") or {}
    unit = (serving.get("unit") or "").strip()
    if record.get("source_collection") == "SWASTHAM_INDIAN_MEAL_LIBRARY":
        return f"per {unit}" if unit else None
    if unit.lower() == "100 g" or serving.get("weight_g") == 100.0:
        return "per 100 g"
    return None


def source_label(library: Library, record_id: str) -> str:
    if library.is_indian(record_id):
        return library.indian_flag or "Indian library"
    return "USDA"


def make_fact(library: Library, record_id: str, field: str) -> Fact | None:
    """One locked fact, or None when the data can't support it (never a guess)."""
    rec = library.get(record_id)
    if rec is None:
        return None
    flags = library.flags.get(record_id, set())
    if FLAG_MACRO_MISMATCH in flags:
        return None
    if FLAG_SERVING_UNCLEAR in flags:
        return None   # per-serving values can't be trusted and there is no gram weight to fall back on
    serving = _serving_text(rec)
    if serving is None or field not in FIELD_LABELS:
        return None
    constraint = ((rec.get("data_quality") or {}).get("usage_constraint") or "").lower()
    unit, label = FIELD_LABELS[field]
    # "Do not present carbohydrate and fibre together" is honoured by the caller: one field per fact, and
    # services.pick_fact never pairs the two for such a record.
    value = (rec.get("nutrients_per_serving") or {}).get(field)
    if field == "energy_kcal":
        rng = rec.get("energy_estimate_range_per_serving")
        if "range only" in constraint or FLAG_ENERGY_MISSING in flags:
            if not rng:
                return None
            text = f"about {_num(rng['low_kcal'], 'kcal')}–{_num(rng['high_kcal'], 'kcal')} kcal {serving}"
            return Fact(record_id, field, None, text, serving, source_label(library, record_id), rec["food_name"])
    if value is None:
        return None
    amount = f"{_num(value, unit)} {unit}" if unit == "kcal" else f"{_num(value, unit)} g {label}"
    text = f"about {amount} {serving}"
    return Fact(record_id, field, float(value), text, serving, source_label(library, record_id), rec["food_name"])


def preferred_records(food: dict, library: Library) -> list[str]:
    """The food master's chosen records for this environment, keeping only those loaded and usable."""
    env = library.environment
    ids = (food.get("fact_records") or {}).get(env, [])
    return [rid for rid in ids if library.usable(rid)]
