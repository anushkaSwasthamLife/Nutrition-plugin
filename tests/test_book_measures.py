"""The client's book measures (Healthful Diet, via workflow.html section 3b): a DRAFT reference, never used for answers yet."""
import copy
import json
import shutil
from pathlib import Path

import pytest

import dixit_answer.loader as loader
from dixit_answer.config import DATA_DIR
from dixit_answer.loader import LoadError, load_manifest, load_swastham_data
from dixit_answer.readiness import check

FILE = DATA_DIR / "book_measures_draft.json"
BOOK = json.loads(FILE.read_text(encoding="utf-8"))


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_file_is_registered_and_a_draft():
    assert BOOK["status"] == "DRAFT"
    assert load_manifest()["swastham_data"]["book_measures"] == "book_measures_draft.json"


def test_readiness_lists_it_as_unsigned():
    problems = [i for i in check()["items"] if i["area"] == "book_measures"]
    assert problems and "DRAFT" in problems[0]["problem"]


def test_production_refuses_a_draft_file_even_when_everything_else_is_signed(tmp_path, settings, monkeypatch):
    shutil.copytree(DATA_DIR, tmp_path / "data")
    manifest = json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8"))
    for key, name in manifest["swastham_data"].items():
        if key == "book_measures":
            continue
        doc = json.loads((tmp_path / "data" / name).read_text(encoding="utf-8"))
        doc["status"] = "APPROVED"
        (tmp_path / "data" / name).write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path / "data")
    prod = copy.copy(settings)
    prod.environment = "production"
    with pytest.raises(LoadError, match="book_measures_draft.json"):
        load_swastham_data(prod, manifest)


def test_development_loads_it(settings):
    assert load_swastham_data(settings).book_measures["version"] == BOOK["version"]


# ---------------------------------------------------------------- the content is what the page says, with a page for every line
def test_every_line_cites_a_page_and_its_text():
    rows = BOOK["household_measures"] + BOOK["equivalents"] + BOOK["per_meal_and_day"]["rows"]
    assert len(rows) == 5 + 6 + 5
    for row in rows:
        assert str(row.get("page", "")).strip() and str(row.get("text", "")).strip(), row


def test_household_measures_agree_with_each_other():
    m = by_id(BOOK["household_measures"])
    assert (m["vati"]["amount"], m["vati"]["unit"]) == (60, "g")
    assert (m["teaspoon"]["amount"], m["tablespoon"]["amount"], m["ounce"]["amount"]) == (5, 15, 28.3)
    assert m["tablespoon"]["amount"] == m["teaspoon"]["amount"] * m["tablespoon"]["teaspoons"]
    assert abs(m["cup"]["amount"] - m["cup"]["about_vati"] * m["vati"]["amount"]) <= 0.05 * m["cup"]["amount"]      # "about 2 vati"


def test_per_meal_is_half_a_day_and_the_oil_adds_up():
    rows = by_id([{**r, "id": r["nutrient"]} for r in BOOK["per_meal_and_day"]["rows"]])
    for nutrient in ("protein_g", "carbohydrate_g", "oil_ml", "energy_kcal"):
        assert rows[nutrient]["per_meal"] * 2 == rows[nutrient]["per_day"], nutrient
    assert rows["oil_ml"]["per_meal"] == 5 * by_id(BOOK["household_measures"])["teaspoon"]["amount"]
    oil = by_id(BOOK["equivalents"])["oil_for_a_family_of_four"]
    assert sum(oil["tablespoons"].values()) == 6 and abs(oil["oil_ml"] - 6 * 15) <= 10      # "about 6 tablespoons"
    assert "scale" in BOOK["per_meal_and_day"]["basis"]


def test_equivalents_match_the_page():
    e = by_id(BOOK["equivalents"])
    assert e["carbohydrate_serving"]["carbohydrate_g"] == 23.4 and e["carbohydrate_serving"]["wheat_g"] == 30
    assert (e["hidden_carbohydrate"]["carbohydrate_g"], e["hidden_carbohydrate"]["protein_g"]) == (20, 7)
    assert e["same_as_one_chapati"]["items"][0] == "1 vati cooked rice" and len(e["same_as_one_chapati"]["items"]) == 8
    assert e["about_20_g_protein"]["protein_g"] == 20 and len(e["about_20_g_protein"]["items"]) == 5
    assert e["one_meal_unit_example"]["units_allowed"] == 3


# ---------------------------------------------------------------- the listed conflicts are real today
def test_conflicts_name_a_decision_and_are_still_true():
    conflicts = by_id(BOOK["conflicts_with_current_system"])
    assert set(conflicts) == {"C1", "C2", "C3", "C4"}
    for c in conflicts.values():
        assert all(str(c.get(k, "")).strip() for k in ("topic", "book", "system", "effect", "decision_needed")), c
    white = json.loads((DATA_DIR / "white_lists.json").read_text(encoding="utf-8"))
    cup = by_id(white["units"]["entries"])["cup"]
    assert cup["fixed_ml"] == 240 and by_id(BOOK["household_measures"])["cup"]["amount"] == 125          # C1: change either and update this
    vati = by_id(white["units"]["entries"])["vati"]                                                       # C2: vati only for the book's recipes
    assert vati["grams"] == 60 and vati["allowed_in"] == ["development"]
    limits = json.loads((DATA_DIR / "condition_limits_test.json").read_text(encoding="utf-8"))["limits"]
    assert any(r["condition"] == "general" and r["nutrient"] == "energy_kcal" and r["daily_max"] == 2000 for r in limits)   # C4
    food = json.loads((DATA_DIR / "food_master.json").read_text(encoding="utf-8"))["foods"]
    chapati = next(f for f in food if f["id"] == "FOOD-CHAPATI")
    assert not chapati.get("unit_weights_g")                                                                # C3: no per-piece weight
