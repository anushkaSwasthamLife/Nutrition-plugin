"""Compare this calculator with INDB's published values for every INDB recipe (INDB plan, phase 2, first deliverable).

Run:  .venv\\Scripts\\python.exe -m dixit_answer.indb_compare [--out DIR]

Each recipe is calculated from its own ingredient rows, with USDA values (through the library's Ingredient Map) for IFCT-coded
ingredients and INDB's UK and US tables for the rest, then compared with INDB's per-100 g values. It writes recipe_comparison.csv
and SUMMARY.md, and changes nothing else. The error measured here is the difference from INDB, which is itself calculated, so
it shows how far a mapped-to-USDA calculation lands from a published calculation, not how far either is from a lab value.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from .config import DEFAULT_LIBRARY_DIR, PROJECT_DIR
from .indb_calc import NUTRIENTS, STRICT, WITH_PROXIES, Sources, calculate, load_fct, load_ingredient_map, load_usda

NUTRITION_DIR = PROJECT_DIR.parent / "Nutrition"
ENERGY_FLOOR = 5.0          # kcal per 100 g; below this a percentage means nothing (water, tea)
MACRO_FLOOR = 1.0           # g per 100 g
BUCKETS = [(0.99, "99-100%"), (0.95, "95-99%"), (0.90, "90-95%"), (0.0, "below 90%")]
INDB_COLUMNS = {"energy_kcal": "energy_kcal", "protein_g": "protein_g", "fat_g": "fat_g", "carb_g": "carb_g", "fibre_g": "fibre_g"}


# ---------------------------------------------------------------- one recipe
def pct_error(ours: float, theirs: float, floor: float) -> float | None:
    """Signed percentage difference from INDB, or None when INDB's value is too small for a percentage to mean anything."""
    if theirs is None or theirs < floor:
        return None
    return (ours - theirs) / theirs * 100.0


def compare_recipe(rows: list[dict], indb: dict, sources: Sources) -> dict:
    result = calculate(rows, sources)
    out = {"complete": result.complete, "coverage": result.coverage, "weight_g": result.weight_g,
           "unvalued": "; ".join(f"{n} ({g:g} g: {why})" for n, g, why in result.unvalued),
           "unweighed": "; ".join(f"{n} [{u or 'no unit'}]" for n, u in result.unweighed)}
    per100 = result.per_100g()
    out["energy_ours"] = None if per100 is None else per100["energy_kcal"]
    out["energy_indb"] = indb.get("energy_kcal")
    out["energy_err_pct"] = None
    for n in NUTRIENTS:
        out[f"{n}_diff"] = None
    if per100 is not None:
        out["energy_err_pct"] = pct_error(per100["energy_kcal"], indb.get("energy_kcal"), ENERGY_FLOOR)
        for n in NUTRIENTS:
            if indb.get(n) is not None:
                out[f"{n}_diff"] = per100[n] - indb[n]
    return out


DOMINANT_SHARE = 0.30       # an ingredient "dominates" a recipe when it gives at least this share of the energy
MIN_RECIPES = 3


def dominant_ingredients(rows: list[dict], sources: Sources) -> list[tuple[str, float]]:
    result = calculate(rows, sources)
    total = sum(p[3] for p in result.parts)
    if total <= 0:
        return []
    by_code: dict[str, float] = defaultdict(float)
    for code, _name, _grams, kcal in result.parts:
        by_code[code] += kcal
    return [(c, k / total) for c, k in by_code.items() if k / total >= DOMINANT_SHARE]


def suspect_ingredients(shares: dict[str, list[float]], names: dict[str, str], imap: dict) -> list[list]:
    """Mapped ingredients that give a big share of a recipe's energy, sorted by how far those recipes drift from INDB."""
    out = []
    for code, errors in shares.items():
        if len(errors) >= MIN_RECIPES:
            entry = imap[code]
            out.append([code, names.get(code, ""), entry["match"], entry["usda_id"], len(errors), round(statistics.median(errors), 1)])
    return sorted(out, key=lambda r: -abs(r[5]))


# ---------------------------------------------------------------- statistics
def stats(errors: list[float]) -> dict:
    """Summary of absolute percentage errors."""
    a = sorted(abs(e) for e in errors)
    if not a:
        return {"n": 0}
    pick = lambda q: a[min(len(a) - 1, int(q * len(a)))]
    return {"n": len(a), "median": statistics.median(a), "p90": pick(0.90), "max": a[-1],
            "within10": sum(x <= 10 for x in a) / len(a), "within15": sum(x <= 15 for x in a) / len(a),
            "within25": sum(x <= 25 for x in a) / len(a)}


def bucket_name(coverage: float | None) -> str:
    if coverage is None:
        return "no weight"
    for low, name in BUCKETS:
        if coverage >= low:
            return name
    return "below 90%"


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "| 0 | | | | | | |"
    return (f"| {s['n']} | {s['median']:.1f}% | {s['p90']:.1f}% | {s['max']:.0f}% | {s['within10']:.0%} | "
            f"{s['within15']:.0%} | {s['within25']:.0%} |")


HEAD = "| Recipes | Median error | 90th percentile | Worst | Within 10% | Within 15% | Within 25% |\n|---|---|---|---|---|---|---|"


def summarise(rows: list[dict], suspects: list[list] | None = None) -> str:
    """Markdown report from the per-recipe rows (each carries both modes)."""
    lines = [f"# INDB comparison, phase 2 ({date.today().isoformat()})", "",
             "Error = this calculator's energy per 100 g minus INDB's, as a percentage of INDB's. INDB is itself a calculation "
             "(IFCT values), so this measures distance from INDB, not from a lab value. Recipes whose INDB energy is below "
             f"{ENERGY_FLOOR:g} kcal per 100 g are left out of percentages (water-like drinks).", ""]
    total = len(rows)
    for mode, label in (("strict", "Strict: only 'same food' USDA matches"),
                        ("proxy", "With proxies: same food, close proxy and weak proxy")):
        done = [r for r in rows if r[f"{mode}_complete"]]
        incomplete = total - len(done)
        lines += [f"## {label}", "",
                  f"{total} INDB recipes. **{len(done)} could be calculated in full; {incomplete} are incomplete** "
                  "(an ingredient has no usable value or no gram weight) and would show no number.", ""]
        errs = [r[f"{mode}_err"] for r in done if r[f"{mode}_err"] is not None]
        lines += ["Recipes calculated in full:", "", HEAD, _fmt(stats(errs)), ""]
        # what the answer would look like if we also accepted recipes with small gaps
        lines += ["Recipes with gaps, by share of weight (water left out) that has a value. Missing ingredients are counted as zero:",
                  "", "| Value covers | " + HEAD.split("\n")[0][2:], "|---|" + HEAD.split("\n")[1][1:]]
        for _low, name in BUCKETS:
            group = [r[f"{mode}_err"] for r in rows if not r[f"{mode}_complete"] and bucket_name(r[f"{mode}_coverage"]) == name
                     and r[f"{mode}_err"] is not None]
            lines.append(f"| {name} {_fmt(stats(group))}")
        lines.append("")
        by_source = defaultdict(list)
        for r in done:
            if r[f"{mode}_err"] is not None:
                by_source[r["recipe_code"][:3]].append(r[f"{mode}_err"])
        lines += ["Complete recipes by cookbook:", "", "| Cookbook " + HEAD.split("\n")[0], "|---|" + HEAD.split("\n")[1][1:]]
        for key in sorted(by_source):
            lines.append(f"| {key} {_fmt(stats(by_source[key]))}")
        lines.append("")
    control = [r for r in rows if r["only_fct"] and r["strict_complete"] and r["strict_err"] is not None]
    lines += ["## Arithmetic check (recipes whose ingredients all come from INDB's own UK and US tables)", "",
              "These need no USDA mapping, so the result should match INDB almost exactly. A large error here means the unit rules "
              "or the adding-up are wrong.", "", HEAD, _fmt(stats([r["strict_err"] for r in control])), ""]
    worst = sorted((r for r in rows if r["proxy_complete"] and r["proxy_err"] is not None), key=lambda r: -abs(r["proxy_err"]))[:25]
    if suspects:
        lines += ["## Ingredients whose recipes drift furthest from INDB", "",
                  f"Ingredients that give at least {DOMINANT_SHARE:.0%} of a recipe's energy, in at least {MIN_RECIPES} complete recipes. "
                  "A large signed error means this USDA match gives a different energy than INDB's IFCT value; a reviewer should "
                  "look at these mappings first (full list in ingredient_suspects.csv).", "",
                  "| Code | Ingredient | Match | USDA record | Recipes | Median signed error |", "|---|---|---|---|---|---|"]
        for code, name, match, usda_id, n, err in suspects[:15]:
            lines.append(f"| {code} | {name} | {match} | {usda_id} | {n} | {err:+.1f}% |")
        lines.append("")
    lines += ["## 25 largest errors among complete recipes (with proxies)", "",
              "| Recipe | Name | INDB kcal/100 g | Ours | Error |", "|---|---|---|---|---|"]
    for r in worst:
        lines.append(f"| {r['recipe_code']} | {r['recipe_name']} | {r['energy_indb']:.0f} | {r['proxy_energy']:.0f} | {r['proxy_err']:+.0f}% |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- data in, files out
def _table(path: Path) -> list[dict]:
    import openpyxl
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(workbook.active.iter_rows(values_only=True))
    workbook.close()
    head = [str(h) for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:] if any(c is not None for c in r)]


def latest_ingredient_map(nutrition_dir: Path) -> Path:
    found = sorted((nutrition_dir / "releases").glob("*/ingredient_map.csv"))
    if not found:
        raise FileNotFoundError(f"no ingredient_map.csv under {nutrition_dir / 'releases'}")
    return found[-1]


def run(indb_dir: Path, library_dir: Path, ingredient_map: Path, out_dir: Path) -> dict:
    usda = load_usda(library_dir)
    imap = load_ingredient_map(ingredient_map)
    fct = {**load_fct(indb_dir / "UK_fct.xlsx", "uk_cofid"), **load_fct(indb_dir / "US_fct.xlsx", "us_fct")}
    strict = Sources(usda=usda, ingredient_map=imap, fct=fct, allowed_matches=STRICT)
    proxy = Sources(usda=usda, ingredient_map=imap, fct=fct, allowed_matches=WITH_PROXIES)

    indb_rows = {str(r["food_code"]): r for r in _table(indb_dir / "INDB.xlsx") if r.get("food_code")}
    by_recipe: dict[str, list[dict]] = defaultdict(list)
    for row in _table(indb_dir / "recipes.xlsx"):
        if row.get("recipe_code"):
            by_recipe[str(row["recipe_code"])].append(row)

    out_rows = []
    shares: dict[str, list[float]] = defaultdict(list)       # INDB ingredient code -> signed errors of recipes it dominates
    names: dict[str, str] = {}
    for code, reference in sorted(indb_rows.items()):
        rows = by_recipe.get(code, [])
        indb = {n: (float(reference[INDB_COLUMNS[n]]) if isinstance(reference.get(INDB_COLUMNS[n]), (int, float)) else None)
                for n in NUTRIENTS}
        a, b = compare_recipe(rows, indb, strict), compare_recipe(rows, indb, proxy)
        if b["complete"] and b["energy_err_pct"] is not None:
            for ing_code, share in dominant_ingredients(rows, proxy):
                if ing_code in imap:
                    shares[ing_code].append(b["energy_err_pct"])
                    names[ing_code] = imap[ing_code]["name"]
        kinds = {proxy.resolve(str(r.get("food_code") or ""), str(r.get("food_code_org") or "")).kind for r in rows}
        out_rows.append({
            "recipe_code": code, "recipe_name": str(reference.get("food_name", "")).strip(),
            "ingredient_rows": len(rows), "only_fct": bool(rows) and kinds <= {"uk_cofid", "us_fct", "additive"},
            "energy_indb": indb["energy_kcal"], "weight_g": b["weight_g"],
            "strict_complete": a["complete"], "strict_coverage": a["coverage"], "strict_energy": a["energy_ours"], "strict_err": a["energy_err_pct"],
            "proxy_complete": b["complete"], "proxy_coverage": b["coverage"], "proxy_energy": b["energy_ours"], "proxy_err": b["energy_err_pct"],
            "proxy_protein_diff": b["protein_g_diff"], "proxy_fat_diff": b["fat_g_diff"], "proxy_carb_diff": b["carb_g_diff"],
            "proxy_fibre_diff": b["fibre_g_diff"], "unvalued_strict": a["unvalued"], "unvalued_proxy": b["unvalued"],
            "unweighed": b["unweighed"],
        })
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "recipe_comparison.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out_rows[0]))
        writer.writeheader()
        for row in out_rows:
            writer.writerow({k: (f"{v:.2f}" if isinstance(v, float) else v) for k, v in row.items()})
    suspects = suspect_ingredients(shares, names, imap)
    with open(out_dir / "ingredient_suspects.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["indb_code", "ingredient", "match_type", "usda_record", "recipes_it_dominates", "median_signed_error_pct"])
        writer.writerows(suspects)
    (out_dir / "SUMMARY.md").write_text(summarise(out_rows, suspects), encoding="utf-8")
    return {"recipes": len(out_rows), "out": str(out_dir)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--indb-dir", type=Path, default=NUTRITION_DIR / "indb_source")
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--ingredient-map", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=NUTRITION_DIR / "indb_phase2")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        imap = args.ingredient_map or latest_ingredient_map(NUTRITION_DIR)
        info = run(args.indb_dir, args.library_dir, imap, args.out)
    except (OSError, KeyError, ValueError) as exc:
        print(f"cannot run the comparison: {exc.__class__.__name__}: {exc}")
        return 2
    print(f"compared {info['recipes']} recipes; wrote {info['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
