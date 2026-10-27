"""Release bundle loader (phase 1): hash checks, environment switches, eligibility, quarantine and quality gates.

Any pin mismatch stops start-up. Production refuses Indian records without a recorded licence ID, and refuses TEST ONLY data.
Quality gates come from the analysis (D1-D3): impossible fat/energy values, unclear servings, missing energy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_DIR, Settings

# Indian serving units that are a real measure. A count unit ("4 chapati", "3 parantha") with a quantity other than 1 is
# unclear: "Chapati/Roti" says 4 chapati at 73 kcal, which looks like one chapati (D2).
MEASURE_UNITS = {"bowl", "small bowl", "curry bowl", "plate", "small plate", "cup", "tea cup", "glass", "tall glass",
                 "tablespoon", "teaspoon", "portion", "serving", "katori"}

FLAG_MACRO_MISMATCH = "macro_energy_mismatch"   # D1: e.g. 495 g fat in a bowl of kofta
FLAG_SERVING_UNCLEAR = "serving_unclear"        # D2
FLAG_ENERGY_MISSING = "energy_missing"          # D3
FLAG_MACROS_INCOMPLETE = "macros_incomplete"    # D4: energy with no macronutrients behind it (e.g. USDA "Oil, canola":
                                                # 900 kcal, 0 g fat). Facts may show energy; recipe maths refuses it.


class LoadError(RuntimeError):
    """The release bundle failed a check; the app must not start."""


@dataclass
class Library:
    environment: str
    records: dict[str, dict] = field(default_factory=dict)
    flags: dict[str, set[str]] = field(default_factory=dict)
    sources_on: list[str] = field(default_factory=list)
    indian_flag: str = ""
    report: dict = field(default_factory=dict)

    def get(self, record_id: str) -> dict | None:
        return self.records.get(record_id)

    def usable(self, record_id: str) -> bool:
        """Loaded and not blocked by a quality gate (D1)."""
        return record_id in self.records and FLAG_MACRO_MISMATCH not in self.flags.get(record_id, set())

    def is_indian(self, record_id: str) -> bool:
        rec = self.records.get(record_id) or {}
        return rec.get("source_collection") == "SWASTHAM_INDIAN_MEAL_LIBRARY"


@dataclass
class SwasthamData:
    food_master: dict
    approved_list: dict
    cooking_tips: dict
    golden_rules: dict
    word_lists: dict
    allergen_map: dict
    recipes: dict
    condition_limits: dict
    swap_table: dict
    cooking_factors: dict
    rules_registry: dict
    emergency_rules: dict
    harmful_intent_rules: dict
    hinglish_glossary: dict
    reply_translations: dict
    general_recipe_rules: dict
    support_content: dict
    white_lists: dict
    black_lists: dict
    dish_rules: dict
    book_measures: dict
    decision_logic: dict
    doctor_packs: dict
    router_rules: dict
    book_ingredient_map: dict
    manifest: dict
    programme_rules: dict = None          # programme id -> its rules file (manifest "programme_rules"); one per doctor's programme
    food_cautions: dict = None            # condition / medicine / pregnancy food cautions (cautions.py)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "manifest.json").read_text(encoding="utf-8"))


def quality_flags(record: dict) -> set[str]:
    flags: set[str] = set()
    n = record.get("nutrients_per_serving") or {}
    energy, fat = n.get("energy_kcal"), n.get("fat_g") or 0
    carb, protein = n.get("carbohydrate_g") or 0, n.get("protein_g") or 0
    if energy is None:
        flags.add(FLAG_ENERGY_MISSING)
    if fat > 100:
        flags.add(FLAG_MACRO_MISMATCH)
    elif energy is not None and (fat * 9 + carb * 4 + protein * 4) > energy * 1.5 + 25:
        flags.add(FLAG_MACRO_MISMATCH)
    if energy is not None and energy >= 100 and (fat * 9 + carb * 4 + protein * 4) < energy * 0.5:
        flags.add(FLAG_MACROS_INCOMPLETE)
    if record.get("source_collection") == "SWASTHAM_INDIAN_MEAL_LIBRARY":
        serving = record.get("serving") or {}
        unit = (serving.get("unit") or "").strip().lower()
        quantity = serving.get("quantity")
        if not unit or (unit not in MEASURE_UNITS) or quantity not in (1, 1.0):
            flags.add(FLAG_SERVING_UNCLEAR)
    return flags


def load_library(settings: Settings, manifest: dict | None = None) -> Library:
    manifest = manifest or load_manifest()
    env_name = settings.environment
    env = manifest["environments"].get(env_name)
    if env is None:
        raise LoadError(f"Unknown environment '{env_name}'.")
    sources = list(env["sources"])
    if env_name == "production" and "indian" in sources and not env.get("indian_licence_id"):
        raise LoadError("Production config switches on Indian records without a licence ID.")

    files = manifest["library"]["files"]
    base = Path(settings.library_dir)
    for key in ["quarantine", *sources]:
        path = base / files[key]["path"]
        if not path.is_file():
            raise LoadError(f"Library file missing: {path}")
        actual = sha256_of(path)
        if actual != files[key]["sha256"]:
            raise LoadError(f"Hash mismatch for {files[key]['path']}: the library has changed since it was pinned.")

    quarantined: set[str] = set()
    with open(base / files["quarantine"]["path"], encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rec = json.loads(line)
                quarantined.add(rec.get("nutrition_record_id") or rec.get("source_record_id") or "")
    if len(quarantined) != files["quarantine"]["count"]:
        raise LoadError("Quarantine count does not match the manifest.")

    library = Library(environment=env_name, sources_on=sources, indian_flag=env.get("indian_flag", ""))
    counts: dict[str, int] = {}
    for key in sources:
        eligible = 0
        with open(base / files[key]["path"], encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    rid = rec["nutrition_record_id"]
                except (ValueError, KeyError, TypeError) as exc:
                    raise LoadError(f"{files[key]['path']}: unreadable record ({exc.__class__.__name__})") from exc
                if not (rec.get("retrieval") or {}).get("eligible"):
                    continue
                if rid in quarantined or (rec.get("data_quality") or {}).get("runtime_status") == "quarantined":
                    continue
                eligible += 1
                library.records[rid] = rec
                flags = quality_flags(rec)
                if flags:
                    library.flags[rid] = flags
        if eligible != files[key]["eligible_count"]:
            raise LoadError(f"{key}: {eligible} eligible records, manifest says {files[key]['eligible_count']}.")
        counts[key] = eligible

    gate_counts: dict[str, int] = {}
    for flags in library.flags.values():
        for flag in flags:
            gate_counts[flag] = gate_counts.get(flag, 0) + 1
    library.report = {"library_version": manifest["library"]["version"], "environment": env_name, "sources": sources,
                      "eligible": counts, "quality_gates": gate_counts}
    return library


def load_swastham_data(settings: Settings, manifest: dict | None = None) -> SwasthamData:
    manifest = manifest or load_manifest()
    env = manifest["environments"][settings.environment]
    names = manifest["swastham_data"]
    loaded = {}
    for key, name in names.items():
        try:
            loaded[key] = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LoadError(f"{name}: cannot be read ({exc.__class__.__name__})") from exc
        if not isinstance(loaded[key], dict):
            raise LoadError(f"{name}: must be a JSON object")
    rules: dict = {}
    for pid, name in (manifest.get("programme_rules") or {}).items():
        try:
            rules[pid] = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LoadError(f"{name}: cannot be read ({exc.__class__.__name__})") from exc
    if not env.get("allow_test_only_data"):
        checked = {**{names[k]: v for k, v in loaded.items()}, **{manifest["programme_rules"][p]: v for p, v in rules.items()}}
        for name, data in checked.items():
            if str(data.get("status", "")).upper() in {"TEST ONLY", "STUB", "PARTIAL", "PROPOSED", "DRAFT"}:
                raise LoadError(f"Production refuses {name}: status is {data['status']}.")
    return SwasthamData(manifest=manifest, programme_rules=rules, **loaded)
