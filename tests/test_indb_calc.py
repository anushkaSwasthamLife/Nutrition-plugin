"""Recipe calculator and the INDB comparison: unit rules, valuation, completeness, and a regression guard on the real data."""
import math
from pathlib import Path

import pytest

from dixit_answer import indb_calc as calc
from dixit_answer import indb_compare as cmp
from dixit_answer.config import DEFAULT_LIBRARY_DIR

NUTRITION = Path(__file__).resolve().parents[2] / "Nutrition"
INDB_DIR = NUTRITION / "indb_source"


def per100(energy=100.0, protein=10.0, fat=5.0, carb=12.0, fibre=2.0):
    return {"energy_kcal": energy, "protein_g": protein, "fat_g": fat, "carb_g": carb, "fibre_g": fibre}


def sources(allowed=calc.STRICT):
    return calc.Sources(
        usda={"U1": per100(40, 1, 0, 9, 1), "U2": per100(300, 20, 10, 30, 5)},
        ingredient_map={"G017": {"match": "same food", "usda_id": "U1", "name": "Onion"},
                        "B001": {"match": "close proxy", "usda_id": "U2", "name": "Dal"},
                        "L003": {"match": "no USDA food", "usda_id": "", "name": "Paneer"},
                        "X999": {"match": "same food", "usda_id": "MISSING", "name": "Ghost"}},
        fct={"I502": {**per100(400, 0, 0, 100, 0), "_kind": "uk_cofid"}, "K505": {**per100(0, 0, 0, 0, 0), "_kind": "uk_cofid"}},
        allowed_matches=allowed)


def row(code, amount, unit, name="x", org=""):
    return {"food_code": code, "food_code_org": org, "food_name": name, "amount": amount, "unit": unit}


# ---------------------------------------------------------------- unit rules
@pytest.mark.parametrize("amount,unit,code,expected", [
    (50, "g", "G017", 50), (2, "tsp", "G017", 4), (2, "tsp", "C010", 1), (2, "tsp", "T001", 8), (1, "tbsp", "T001", 13.5),
    (1, "tbsp", "F006", 40), (2, "tsp", "I502", 8), (1, "tbsp", "I502", 12), (50, "ml", "L002", 50), (1, "C", "L002", 240),
    (1.5, "C", "K505", 360), (2, "sprig", "G010", 4), (3, "small", "G010", 6), (2, "tsp", "D001", 4), (1, "tbsp", "D001", 7),
])
def test_unit_rules(amount, unit, code, expected):
    assert calc.to_grams(amount, unit, code) == pytest.approx(expected)


def test_a_later_rule_overrides_an_earlier_one_as_in_indb():
    """INDB converts a cup of sugar at 200 g, then a later line sets every cup to 240 g; the later line wins."""
    assert calc.to_grams(1, "C", "I502") == 240


def test_pinch_ignores_the_amount_and_fenugreek_leaves_are_special():
    assert calc.to_grams(5, "pinch", "G001") == 0.125
    assert calc.to_grams(3, "bunch", "C020", "FENUGREEK LEAVES") == 6
    assert calc.to_grams(3, "bunch", "C020", "fenugreek leaves") is None


@pytest.mark.parametrize("amount,unit,code", [
    (1, "nos", "G017"), (1, "", "G017"), (1, "sheet", "G017"), (1, "drops", "G017"), (2, "tsp", "N001"), (2, "sprig", "I502"),
    (None, "g", "G017"), ("5", "g", "G017"), (True, "g", "G017"), (-5, "g", "G017"), (float("nan"), "g", "G017"),
    (float("inf"), "g", "G017"), (1, "G", "G017"), (1, "TSP", "G017"), (1, None, "G017"), (1, "g", None)])
def test_units_nobody_covers_are_not_guessed(amount, unit, code):
    got = calc.to_grams(amount, unit, code)
    if unit == "g" and isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount) and amount >= 0:
        assert got == amount
    else:
        assert got is None


def test_zero_amount_is_zero_grams():
    assert calc.to_grams(0, "g", "G017") == 0 and calc.to_grams(0, "tsp", "T001") == 0


# ---------------------------------------------------------------- reading table cells
@pytest.mark.parametrize("value,expected", [(None, 0.0), ("", 0.0), ("Tr", 0.0), ("N", 0.0), ("NA", 0.0), (" tr ", 0.0), ("12.5", 12.5),
                                            (3, 3.0), (0, 0.0)])
def test_number_reads_trace_marks_as_zero(value, expected):
    assert calc.number(value) == expected


@pytest.mark.parametrize("value", ["abc", "1,5", -1, float("nan"), float("inf"), True, [1], {"a": 1}, "-3", "1e999"])
def test_number_rejects_what_it_cannot_read(value):
    assert calc.number(value) is None


def test_blank_is_not_zero_for_the_library():
    assert calc.number(None, blank_is_zero=False) is None and calc.number("NA", blank_is_zero=False) is None
    assert calc.number(0, blank_is_zero=False) == 0.0


def test_library_record_without_energy_is_unusable_and_fibre_blank_is_zero():
    ok = {"nutrients_per_100g": {"energy_kcal": 40, "protein_g": 1, "fat_g": 0, "carbohydrate_g": 9.3, "fiber_g": None}}
    assert calc._per100_from_library(ok)["carb_g"] == pytest.approx(9.3) or calc._per100_from_library(ok) is None
    for missing in ("energy_kcal", "protein_g", "fat_g"):
        bad = {"nutrients_per_100g": {"energy_kcal": 40, "protein_g": 1, "fat_g": 0, "net_carbohydrate_g": 7, "fiber_g": 1}}
        bad["nutrients_per_100g"][missing] = None
        assert calc._per100_from_library(bad) is None, missing
    assert calc._per100_from_library({}) is None


def test_library_net_carbs_fall_back_to_total_minus_fibre():
    rec = {"nutrients_per_100g": {"energy_kcal": 40, "protein_g": 1, "fat_g": 0, "carbohydrate_g": 9.0, "fiber_g": 2.0}}
    assert calc._per100_from_library(rec)["carb_g"] == 7.0
    rec["nutrients_per_100g"]["fiber_g"] = 20.0
    assert calc._per100_from_library(rec)["carb_g"] == 0.0           # fibre above carbohydrate never gives a negative


# ---------------------------------------------------------------- valuation
def test_resolution_kinds():
    s = sources()
    assert s.resolve("G017", "").kind == "usda"
    assert s.resolve("I502", "").kind == "uk_cofid"
    assert s.resolve("", "").kind == "additive" and s.resolve("None", "").kind == "additive" and s.resolve(None, "").kind == "additive"
    assert "not allowed" in s.resolve("B001", "").reason                   # close proxy, strict run
    assert s.resolve("B001", "") .per100 is None
    assert calc.Sources(**{**s.__dict__, "allowed_matches": calc.WITH_PROXIES}).resolve("B001", "").kind == "usda"
    assert "no USDA" in s.resolve("L003", "").reason or "not allowed" in s.resolve("L003", "").reason
    assert "not in the library" in s.resolve("X999", "").reason
    assert "no USDA mapping" in s.resolve("Q123", "").reason


def test_no_usda_food_is_never_valued_even_when_proxies_are_allowed():
    assert sources(calc.WITH_PROXIES).resolve("L003", "").per100 is None


# ---------------------------------------------------------------- a recipe
def test_hot_tea_style_sum():
    result = calc.calculate([row("G017", 100, "g"), row("I502", 2, "tsp"), row("K505", 1.5, "C", "Water, distilled")], sources())
    assert result.weight_g == pytest.approx(100 + 8 + 360)
    assert result.totals["energy_kcal"] == pytest.approx(40 + 32 + 0)
    assert result.complete and result.coverage == pytest.approx(1.0)
    assert result.per_100g()["energy_kcal"] == pytest.approx(72 / 4.68)


def test_water_counts_in_weight_but_not_in_coverage():
    result = calc.calculate([row("L003", 100, "g", "Paneer"), row("K505", 400, "g", "Water, distilled")], sources())
    assert result.weight_g == 500 and result.nonwater_g == 100 and result.coverage == 0.0 and not result.complete


def test_unvalued_ingredient_makes_recipe_incomplete_and_is_listed():
    result = calc.calculate([row("G017", 100, "g"), row("L003", 50, "g", "Paneer")], sources())
    assert not result.complete and result.unvalued[0][0] == "Paneer" and result.unvalued[0][1] == 50
    assert result.coverage == pytest.approx(100 / 150)
    assert result.totals["energy_kcal"] == 40                               # the gap is not guessed


def test_unweighable_row_is_listed_and_left_out_of_weight():
    result = calc.calculate([row("G017", 100, "g"), row("G017", 2, "nos", "Onion")], sources())
    assert not result.complete and result.unweighed == [("Onion", "nos")] and result.weight_g == 100


def test_additives_without_a_code_change_nothing():
    base = calc.calculate([row("G017", 100, "g")], sources())
    withcolour = calc.calculate([row("G017", 100, "g"), row("", 1, "tsp", "Red colour"), row(None, 5, "g", "Essence")], sources())
    assert withcolour.totals == base.totals and withcolour.weight_g == base.weight_g and withcolour.complete


def test_empty_and_zero_weight_recipes():
    assert calc.calculate([], sources()).per_100g() is None and calc.calculate([], sources()).coverage is None
    zero = calc.calculate([row("G017", 0, "g")], sources())
    assert zero.per_100g() is None and zero.coverage is None


def test_rows_with_missing_fields_do_not_crash():
    result = calc.calculate([{}, {"food_code": "G017"}, {"food_code": "G017", "amount": 10}, {"amount": 5, "unit": "g"}], sources())
    assert isinstance(result.totals, dict)


def test_proxy_run_values_what_strict_run_leaves_out():
    rows = [row("G017", 100, "g"), row("B001", 100, "g")]
    assert not calc.calculate(rows, sources(calc.STRICT)).complete
    both = calc.calculate(rows, sources(calc.WITH_PROXIES))
    assert both.complete and both.totals["energy_kcal"] == 340


def test_huge_amounts_do_not_overflow_to_nonsense():
    result = calc.calculate([row("G017", 1e12, "g")], sources())
    assert math.isfinite(result.totals["energy_kcal"]) and result.per_100g()["energy_kcal"] == pytest.approx(40)


# ---------------------------------------------------------------- comparison helpers
def test_pct_error_and_floors():
    assert cmp.pct_error(110, 100, 5) == 10 and cmp.pct_error(90, 100, 5) == -10
    assert cmp.pct_error(10, 4, 5) is None and cmp.pct_error(10, None, 5) is None and cmp.pct_error(0, 0, 5) is None


def test_stats_edges():
    assert cmp.stats([]) == {"n": 0}
    s = cmp.stats([1, -2, 30])
    assert s["n"] == 3 and s["median"] == 2 and s["max"] == 30 and s["within10"] == pytest.approx(2 / 3)


def test_bucket_names():
    assert cmp.bucket_name(None) == "no weight" and cmp.bucket_name(1.0) == "99-100%" and cmp.bucket_name(0.95) == "95-99%"
    assert cmp.bucket_name(0.91) == "90-95%" and cmp.bucket_name(0.2) == "below 90%" and cmp.bucket_name(0.0) == "below 90%"


def test_compare_recipe_against_a_reference():
    rows = [row("G017", 100, "g")]
    out = cmp.compare_recipe(rows, {"energy_kcal": 44.0, "protein_g": 1.0, "fat_g": 0.0, "carb_g": 9.0, "fibre_g": 1.0}, sources())
    assert out["complete"] and out["energy_err_pct"] == pytest.approx((40 - 44) / 44 * 100)
    gap = cmp.compare_recipe([row("L003", 10, "g", "Paneer")], {"energy_kcal": 100.0}, sources())
    assert not gap["complete"] and "Paneer" in gap["unvalued"]
    empty = cmp.compare_recipe([], {"energy_kcal": 100.0}, sources())
    assert empty["energy_ours"] is None and empty["energy_err_pct"] is None


def test_dominant_ingredient_needs_a_third_of_the_energy():
    rows = [row("G017", 100, "g"), row("B001", 100, "g")]
    s = sources(calc.WITH_PROXIES)
    assert [c for c, _ in cmp.dominant_ingredients(rows, s)] == ["B001"]
    assert cmp.dominant_ingredients([], s) == []


def test_summary_survives_tiny_and_empty_input():
    base = {"recipe_code": "ASC001", "recipe_name": "x", "only_fct": False, "energy_indb": 100.0}
    row_ = {**base, **{f"{m}_{k}": v for m in ("strict", "proxy") for k, v in
                       (("complete", True), ("coverage", 1.0), ("energy", 90.0), ("err", -10.0))}}
    text = cmp.summarise([row_], [["G017", "Onion", "same food", "U1", 3, -9.0]])
    assert "Strict" in text and "Onion" in text
    assert isinstance(cmp.summarise([], None), str)


def test_cli_reports_a_missing_folder_instead_of_crashing(tmp_path, capsys):
    code = cmp.main(["--indb-dir", str(tmp_path / "none"), "--out", str(tmp_path / "out"), "--ingredient-map", str(tmp_path / "x.csv")])
    assert code == 2 and "cannot run" in capsys.readouterr().out


def test_load_fct_reads_trace_marks_and_skips_unreadable_rows(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["retention_factor", "food_code_org", "food_code", "food_name", "primarysource", "energy_kj", "energy_kcal", "carb_g",
               "protein_g", "fat_g", "freesugar_g", "fibre_g"])
    ws.append([None, "UK-1", "G509", "Agar", "ukfct", "67", "16", "Tr", "1.3", "1.2", "Tr", None])
    ws.append([None, "UK-2", "G510", "Bad", "ukfct", "1", "abc", "1", "1", "1", "1", "1"])
    ws.append([None, "UK-3", None, "NoCode", "ukfct", "1", "1", "1", "1", "1", "1", "1"])
    path = tmp_path / "t.xlsx"
    wb.save(path)
    out = calc.load_fct(path, "uk_cofid")
    assert set(out) == {"G509"} and out["G509"]["carb_g"] == 0.0 and out["G509"]["fibre_g"] == 0.0 and out["G509"]["_kind"] == "uk_cofid"


# ---------------------------------------------------------------- regression guard on the real data
needs_data = pytest.mark.skipif(not (INDB_DIR / "recipes.xlsx").exists() or not (DEFAULT_LIBRARY_DIR / "01_data").exists()
                                or not list((NUTRITION / "releases").glob("*/ingredient_map.csv")),
                                reason="INDB source files, library or ingredient map are not on this PC")


@needs_data
def test_real_comparison_arithmetic_matches_indb_and_accuracy_does_not_slip(tmp_path):
    info = cmp.run(INDB_DIR, DEFAULT_LIBRARY_DIR, cmp.latest_ingredient_map(NUTRITION), tmp_path)
    assert info["recipes"] == 1014
    import csv
    rows = list(csv.DictReader(open(tmp_path / "recipe_comparison.csv", encoding="utf-8-sig")))
    control = [abs(float(r["strict_err"])) for r in rows if r["only_fct"] == "True" and r["strict_complete"] == "True" and r["strict_err"]]
    assert len(control) >= 8 and max(control) < 1.0                       # UK/US-only recipes: the adding-up must match INDB
    done = [abs(float(r["proxy_err"])) for r in rows if r["proxy_complete"] == "True" and r["proxy_err"]]
    done.sort()
    assert len(done) > 700 and done[len(done) // 2] < 5.0 and sum(x <= 25 for x in done) / len(done) > 0.95
    assert (tmp_path / "SUMMARY.md").read_text(encoding="utf-8").startswith("# INDB comparison")
