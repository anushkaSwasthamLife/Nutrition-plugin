"""Plan item G tooling: the 100-recipe dietitian review before AI recipe drafting goes live in production.

  python -m dixit_answer.sample_review make  [--out DIR] [--size N]            draw the sample, write sample.json + scoring_sheet.csv
  python -m dixit_answer.sample_review score SHEET.csv --sample sample.json    check the dietitian's sheet against the pass mark

`make` runs people (conditions x allergies x diet x pantry) through the real recipe flow, with every safety check on, and keeps the
recipes the AI drafted. `score` never edits a file or switches anything on: setting recipe_ai_drafting in manifest.json stays a
manual step once this prints PASSED. The pass mark lives in data/sample_review_rules.json and must be signed before `make`.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sys
from datetime import date
from pathlib import Path

from .config import DATA_DIR
from .person import JAIN_EXCLUDED, Person
from .sign_off import _real_date, _text, _unfilled, file_problems

RULES_PATH = DATA_DIR / "sample_review_rules.json"
COLUMNS = ["case_id", "person", "pantry", "recipe", "ingredients", "steps", "totals", "safe", "sensible", "cookable",
           "comments", "reviewer", "review_date"]
MAX_TRIES_FACTOR = 40


class SampleError(Exception):
    pass


def load_rules(path: Path = RULES_PATH) -> dict:
    try:
        rules = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SampleError(f"rules file cannot be read ({exc.__class__.__name__})") from exc
    if not isinstance(rules, dict):
        raise SampleError("rules file is not a JSON object")
    for key in ("sample_size", "conditions", "allergies", "diet_types", "coverage", "pass_mark", "seed"):
        if key not in rules:
            raise SampleError(f"rules file has no '{key}'")
    size = rules["sample_size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise SampleError("sample_size must be a whole number of at least 1")
    return rules


def rules_hash(path: Path = RULES_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signature(case: dict) -> tuple:
    return (tuple(case["conditions"]), tuple(case["allergies"]), case["diet"], tuple(case["pantry"]))


def _draw_case(rng: random.Random, rules: dict, cookable: list[str], want: tuple | None = None) -> dict:
    """A random person; `want` ("condition", x) / ("allergy", x) / ("no_allergy",) / ("two_conditions",) forces one group in."""
    others = [c for c in rules["conditions"] if c != "general"]
    conditions = ["general"] if rng.random() < 0.3 or not others else rng.sample(others, min(len(others), rng.choice([1, 1, 2])))
    allergies = [] if rng.random() < 0.3 or not rules["allergies"] else rng.sample(rules["allergies"], min(len(rules["allergies"]), rng.choice([1, 1, 2])))
    if want:
        if want[0] == "condition":
            conditions = sorted({*[c for c in conditions if c != "general"], want[1]}) if want[1] != "general" else ["general"]
        elif want[0] == "allergy":
            allergies = sorted({*allergies, want[1]})
        elif want[0] == "no_allergy":
            allergies = []
        elif want[0] == "two_conditions" and len(others) >= 2:
            conditions = sorted(rng.sample(others, 2))
    return {"conditions": sorted(conditions), "allergies": sorted(allergies), "diet": rng.choice(rules["diet_types"]),
            "pantry": sorted(rng.sample(cookable, min(len(cookable), rng.choice([1, 2, 2, 3]))))}


def _unmet_groups(cases: list, rules: dict) -> list[tuple]:
    """Groups the rules ask for that the recipes drawn so far do not yet cover enough."""
    need = rules["coverage"]
    unmet = []
    for cond in rules["conditions"]:
        if sum(1 for c in cases if cond in c["conditions"]) < need["min_per_condition"]:
            unmet.append(("condition", cond))
    for allergy in rules["allergies"]:
        if sum(1 for c in cases if allergy in c["allergies"]) < need["min_per_allergy"]:
            unmet.append(("allergy", allergy))
    if sum(1 for c in cases if not c["allergies"]) < need["min_with_no_allergy"]:
        unmet.append(("no_allergy",))
    if sum(1 for c in cases if len([x for x in c["conditions"] if x != "general"]) >= 2) < need["min_with_two_conditions"]:
        unmet.append(("two_conditions",))
    return unmet


def verify_case(engine, case: dict) -> list[str]:
    """Re-check one drawn recipe straight from the raw data files, without the recipe service: allergens, diet, limits."""
    problems = []
    foods = engine.index.foods
    amap = engine.data.allergen_map["allergens"]
    used = [i["food"] for i in case["recipe"]["ingredients"]]
    for fid in used:
        if fid not in foods:
            problems.append(f"{case['case_id']}: {fid} is not in the food list")
            continue
        for allergen in case["allergies"]:
            if fid in amap.get(allergen, {}).get("foods", []) or allergen in foods[fid].get("allergens", []):
                problems.append(f"{case['case_id']}: {fid} contains {allergen}")
        category = foods[fid]["category"]
        if case["diet"] in ("vegetarian", "jain") and category in {"egg", "meat", "fish"}:
            problems.append(f"{case['case_id']}: {fid} is not vegetarian")
        if case["diet"] == "jain" and fid in JAIN_EXCLUDED:
            problems.append(f"{case['case_id']}: {fid} is not allowed for a Jain diet")
    for row in engine.data.condition_limits["limits"]:
        if row["condition"] in case["conditions"] and row.get("daily_max") is not None:
            total = case["recipe"]["totals"].get(row["nutrient"])
            if total is not None and float(total) > float(row["daily_max"]):
                problems.append(f"{case['case_id']}: {row['nutrient']} {total} is over the {row['condition']} limit {row['daily_max']}")
    return problems


def make(engine, rules: dict | None = None, size: int | None = None, rules_path: Path = RULES_PATH) -> dict:
    """Draw people until `size` of them get an AI-drafted recipe that passed the gate. Returns the sample document."""
    rules = rules or load_rules(rules_path)
    size = rules["sample_size"] if size is None else size
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise SampleError("the sample size must be a whole number of at least 1")
    service = engine.recipes
    if not service.ai_enabled:
        raise SampleError("AI recipe drafting is off in this environment, so there is nothing to sample (it is on in development)")
    cookable = sorted(f for f, food in engine.index.foods.items() if f not in engine.index.book_only
                      and food.get("ingredient_record") and food["category"] in {"vegetable", "pulse", "starchy_vegetable"})
    if not cookable:
        raise SampleError("the food list has no vegetables or pulses to cook with")
    rng = random.Random(rules["seed"])
    cases, seen, tries = [], set(), 0
    while len(cases) < size and tries < size * MAX_TRIES_FACTOR:
        tries += 1
        unmet = _unmet_groups(cases, rules)
        case = _draw_case(rng, rules, cookable, rng.choice(unmet) if unmet and rng.random() < 0.7 else None)
        if _signature(case) in seen:
            continue
        seen.add(_signature(case))
        person = Person(allergies=list(case["allergies"]), diet_type=case["diet"],
                        conditions=[c for c in case["conditions"] if c != "general"])
        out = service.run(person=person, have=list(case["pantry"]), dish_ids=[], unknown_words=[], suggestion=None,
                          wants_list=False, home_foods=[])
        recipe = out.recipe
        if out.status != "recipe" or recipe is None or recipe.origin != "ai_suggested" or not (recipe.gate and recipe.gate.passed):
            continue
        cases.append({"case_id": f"G-{len(cases) + 1:03d}", **case, "recipe": {
            "name": recipe.name,
            "ingredients": [{"food": i["food"], "name": engine.index.name(i["food"]), "grams": str(i["grams"])}
                            for i in recipe.ingredients],
            "steps": list(recipe.steps), "totals": {k: str(v) for k, v in recipe.totals.items()},
            "repairs": [str(r) for r in recipe.repairs], "ai_attempts": out.ai_attempts}})
    broken = [p for case in cases for p in verify_case(engine, case)]
    if broken:
        raise SampleError("the recipe flow let through a recipe that breaks a rule, stop and report this: " + "; ".join(broken[:5]))
    shortfall = _coverage_problems({c["case_id"]: c for c in cases}, rules)
    return {"shortfall": shortfall, "rules_version": rules.get("version"), "rules_sha256": rules_hash(rules_path), "rules_status": rules.get("status"),
            "requested": size, "tries": tries, "cases": cases,
            "environment": engine.settings.environment, "llm": getattr(engine.llm, "name", "unknown")}


def sheet_csv(sample: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for case in sample["cases"]:
        r = case["recipe"]
        writer.writerow([case["case_id"],
                         f"conditions: {', '.join(case['conditions'])}; allergies: {', '.join(case['allergies']) or 'none'}; diet: {case['diet']}",
                         ", ".join(case["pantry"]), r["name"],
                         "; ".join(f"{i['name']} {i['grams']} g" for i in r["ingredients"]),
                         " | ".join(r["steps"]), "; ".join(f"{k} {v}" for k, v in r["totals"].items()),
                         "", "", "", "", "", ""])
    return buf.getvalue()


# ----------------------------------------------------------------------------------------------- scoring
def _num(value, low: int, high: int):
    text = _text(value)
    if not text.isascii() or not text.isdigit():
        return None
    number = int(text)
    return number if low <= number <= high else None


def _coverage_problems(cases: dict, rules: dict) -> list[str]:
    problems = []
    coverage = rules["coverage"]

    def count(pick):
        return sum(1 for c in cases.values() if pick(c))

    for cond in rules["conditions"]:
        if count(lambda c: cond in c["conditions"]) < coverage["min_per_condition"]:
            problems.append(f"too few people with '{cond}' in the sample (need {coverage['min_per_condition']})")
    for allergy in rules["allergies"]:
        if count(lambda c: allergy in c["allergies"]) < coverage["min_per_allergy"]:
            problems.append(f"too few people allergic to '{allergy}' in the sample (need {coverage['min_per_allergy']})")
    if count(lambda c: not c["allergies"]) < coverage["min_with_no_allergy"]:
        problems.append("too few people with no allergy in the sample")
    if count(lambda c: len([x for x in c["conditions"] if x != "general"]) >= 2) < coverage["min_with_two_conditions"]:
        problems.append("too few people with two conditions in the sample")
    return problems


def score(sheet_text: str, sample: dict, rules_path: Path = RULES_PATH, today: date | None = None) -> dict:
    """{'verdict': PASSED | PASSED_UNSIGNED | FAILED | INVALID, 'problems': [...], 'numbers': {...}}.
    INVALID means the sheet or sample cannot be judged at all; PASSED_UNSIGNED means the numbers pass but the pass mark file is not signed."""
    today = today or date.today()
    rules = load_rules(rules_path)
    problems: list[str] = []
    if not isinstance(sample, dict) or not isinstance(sample.get("cases"), list):
        return {"verdict": "INVALID", "problems": ["the sample file is damaged"], "numbers": {}}
    if sample.get("rules_sha256") != rules_hash(rules_path):
        problems.append("the pass mark file changed after this sample was drawn; the pass mark must be agreed before the sample is made")
    if sample.get("requested") != rules["sample_size"] or len(sample["cases"]) != rules["sample_size"]:
        problems.append(f"the sample has {len(sample['cases'])} recipes, the rules ask for {rules['sample_size']}")
    try:
        cases = {c["case_id"]: c for c in sample["cases"]}
        problems += _coverage_problems(cases, rules)
    except (KeyError, TypeError):
        return {"verdict": "INVALID", "problems": ["the sample file is damaged"], "numbers": {}}
    if len(cases) != len(sample["cases"]):
        problems.append("the sample repeats a case number")

    try:
        reader = csv.DictReader(io.StringIO(sheet_text.lstrip("﻿")))
        header = set(reader.fieldnames or [])
        rows = list(reader)
    except csv.Error as exc:
        return {"verdict": "INVALID", "problems": [f"the sheet is not a readable CSV ({exc})"], "numbers": {}}
    missing_cols = [c for c in COLUMNS if c not in header]
    if missing_cols:
        return {"verdict": "INVALID", "problems": [f"the sheet has no column(s): {', '.join(missing_cols)}"], "numbers": {}}

    seen: set[str] = set()
    safe_no, sensible, cookable = 0, [], []
    for line, rec in enumerate(rows, start=2):
        cid = _text(rec.get("case_id"))
        if cid not in cases:
            problems.append(f"row {line}: case '{cid}' is not in the sample")
            continue
        if cid in seen:
            problems.append(f"row {line}: case {cid} appears twice")
            continue
        seen.add(cid)
        safe = _text(rec.get("safe")).upper()
        s, k = _num(rec.get("sensible"), 1, 5), _num(rec.get("cookable"), 1, 5)
        if safe not in {"Y", "N"}:
            problems.append(f"{cid}: 'safe' must be Y or N")
        if s is None:
            problems.append(f"{cid}: 'sensible' must be a whole number from 1 to 5")
        if k is None:
            problems.append(f"{cid}: 'cookable' must be a whole number from 1 to 5")
        if _unfilled(rec.get("reviewer")):
            problems.append(f"{cid}: no reviewer name")
        reason = _real_date(rec.get("review_date"), today)
        if reason:
            problems.append(f"{cid}: review_date {reason}")
        if safe == "N":
            safe_no += 1
        if s is not None:
            sensible.append(s)
        if k is not None:
            cookable.append(k)
    for cid in cases:
        if cid not in seen:
            problems.append(f"{cid}: no row in the sheet (every recipe must be scored)")
    if problems:
        return {"verdict": "INVALID", "problems": problems, "numbers": {}}

    n = len(cases)
    mark = rules["pass_mark"]
    numbers = {"recipes": n, "unsafe": safe_no, "mean_sensible": round(sum(sensible) / n, 2),
               "mean_cookable": round(sum(cookable) / n, 2),
               "share_sensible_at_least_3": round(sum(1 for v in sensible if v >= 3) / n, 3),
               "share_cookable_at_least_3": round(sum(1 for v in cookable if v >= 3) / n, 3)}
    fails = []
    if safe_no > mark["max_unsafe"]:
        fails.append(f"{safe_no} recipe(s) marked unsafe (allowed {mark['max_unsafe']})")
    for key, label in (("mean_sensible", "mean 'sensible'"), ("mean_cookable", "mean 'cookable'"),
                       ("share_sensible_at_least_3", "share of 'sensible' of 3 or more"),
                       ("share_cookable_at_least_3", "share of 'cookable' of 3 or more")):
        if numbers[key] < mark["min_" + key]:
            fails.append(f"{label} is {numbers[key]}, needs {mark['min_' + key]}")
    if fails:
        return {"verdict": "FAILED", "problems": fails, "numbers": numbers}
    unsigned = file_problems(Path(rules_path).name, rules)
    return {"verdict": "PASSED" if not unsigned else "PASSED_UNSIGNED", "problems": unsigned, "numbers": numbers}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Plan item G: the 100-recipe dietitian review.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("--out", default="var/sample_review")
    mk.add_argument("--size", type=int)
    sc = sub.add_parser("score")
    sc.add_argument("sheet")
    sc.add_argument("--sample", required=True)
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        if args.cmd == "make":
            from .pipeline import Engine
            rules = load_rules(RULES_PATH)
            if file_problems(RULES_PATH.name, rules):
                print(f"Note: the pass mark file is {rules.get('status')}; the dietitian must sign it before the real review.")
            sample = make(Engine(), rules, args.size, rules_path=RULES_PATH)
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "sample.json").write_text(json.dumps(sample, indent=2, ensure_ascii=False), encoding="utf-8")
            (out / "scoring_sheet.csv").write_text(sheet_csv(sample), encoding="utf-8-sig", newline="")
            print(f"{len(sample['cases'])} of {sample['requested']} recipes drawn ({sample['tries']} people tried) -> {out}")
            for line in sample["shortfall"]:
                print(f"  - {line}")
            return 0 if len(sample["cases"]) == sample["requested"] and not sample["shortfall"] else 1
        sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
        result = score(Path(args.sheet).read_text(encoding="utf-8-sig"), sample, rules_path=RULES_PATH)
    except (SampleError, OSError, ValueError) as exc:
        print(f"Cannot continue: {exc}")
        return 2
    print(f"Verdict: {result['verdict']}")
    for key, value in result["numbers"].items():
        print(f"  {key}: {value}")
    for problem in result["problems"]:
        print(f"  - {problem}")
    return 0 if result["verdict"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
