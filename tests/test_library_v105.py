"""Library v1.0.5: the corrected USDA and Indian values are what the chat reads, and nothing else moved."""
import hashlib
import json
import math
from pathlib import Path

import pytest

from dixit_answer.config import DEFAULT_LIBRARY_DIR

NEW_IDS = {"USDA-2768188", "USDA-2515382", "USDA-2710839", "USDA-2262073", "USDA-2262075", "USDA-2261420"}


def _usda(engine):
    return {r: rec for r, rec in engine.library.records.items() if rec.get("source_collection") == "USDA_NUTRIENT_REFERENCE"}


def _indian(engine):
    return {r: rec for r, rec in engine.library.records.items() if rec.get("source_collection") == "SWASTHAM_INDIAN_MEAL_LIBRARY"}


def test_counts_and_version(engine):
    assert engine.library.report["library_version"] == "1.0.5"
    assert len(_usda(engine)) == 13560 and len(_indian(engine)) == 865
    assert NEW_IDS <= set(_usda(engine))


def test_the_six_new_records_are_complete_and_cite_their_source(engine):
    for rid in NEW_IDS:
        rec = engine.library.records[rid]
        assert rec["food_name"] == rec["aliases"][0] and rec["normalized_name"] == rec["normalized_name"].lower().strip()
        assert rec["serving"] == {"quantity": 1, "unit": "100 g", "weight_g": 100.0, "gram_conversion_available": True}
        assert rec["nutrients_per_100g"] == rec["nutrients_per_serving"]
        assert rec["nutrients_per_100g"]["energy_kcal"] > 0 and rec["lineage"]["citation"].startswith("FDC")
        assert rec["retrieval"]["eligible"] is True


def test_missing_fibre_is_null_never_zero(engine):
    flagged = [r for r in _usda(engine).values() if "Fibre not measured" in ((r["data_quality"].get("usage_constraint")) or "")]
    assert len(flagged) >= 440
    for rec in flagged:
        n = rec["nutrients_per_100g"]
        assert n["fiber_g"] is None and n["net_carbohydrate_g"] is None, rec["nutrition_record_id"]


def test_statuses_match_the_release(engine):
    usda = [r["data_quality"]["runtime_status"] for r in _usda(engine).values()]
    assert usda.count("approved_flagged") == 461 and usda.count("approved") == 13099
    indian = [r["data_quality"]["runtime_status"] for r in _indian(engine).values()]
    assert indian.count("approved") == 642 and indian.count("approved_flagged") == 223


def test_every_indian_recipe_has_a_gram_weight_and_a_check(engine):
    for rid, rec in _indian(engine).items():
        s = rec["serving"]
        assert s["gram_conversion_available"] is True and s["weight_g"] > 0, rid
        assert s["weight_basis"] and s["weight_check"], rid


def test_no_negative_or_non_finite_values_anywhere(engine):
    for rid, rec in engine.library.records.items():
        for part in ("nutrients_per_100g", "nutrients_per_serving"):
            for key, v in rec[part].items():
                assert v is None or (isinstance(v, (int, float)) and math.isfinite(v) and v >= 0), (rid, part, key, v)


def test_fibre_never_exceeds_carbohydrate_unless_flagged(engine):
    for rid, rec in engine.library.records.items():
        n = rec["nutrients_per_100g"]
        if n.get("fiber_g") is not None and n.get("carbohydrate_g") is not None:
            assert (n["fiber_g"] > n["carbohydrate_g"]) == rec["data_quality"]["fiber_exceeds_carbohydrate"], rid


def test_corrected_examples(engine):
    almond = engine.library.records["USDA-2262074"]
    assert almond["nutrients_per_100g"]["energy_kcal"] == 602.5                        # USDA's own value; the earlier 641 was borrowed
    assert almond["data_quality"]["energy_basis"] == "official_specific_2048"
    assert engine.library.records["SURVEY-2707713"]["nutrients_per_100g"]["carbohydrate_g"] == 46.1
    fried = engine.library.records["NUTR-INDB-BFP386"]
    assert fried["data_quality"]["runtime_status"] == "approved_flagged" and "dietitian" in fried["data_quality"]["usage_constraint"]
    assert fried["data_quality"]["requires_approximation_disclosure"] is True


def test_the_library_files_match_their_own_manifest():
    base = Path(DEFAULT_LIBRARY_DIR)
    if not base.exists():
        pytest.skip("library is not on this PC")
    manifest = json.loads((base / "06_release" / "file_manifest.json").read_text(encoding="utf-8"))
    assert manifest["file_count"] == len(manifest["files"])
    for rel, info in manifest["files"].items():
        data = (base / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == info["sha256"] and len(data) == info["bytes"], rel


def test_the_index_agrees_with_the_records(engine):
    base = Path(DEFAULT_LIBRARY_DIR) / "01_data" / "nutrition_master_index.jsonl"
    rows = [json.loads(line) for line in base.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 14425 and len({r["nutrition_record_id"] for r in rows}) == 14425
    for row in rows:
        rec = engine.library.records.get(row["nutrition_record_id"])
        if rec is None:
            assert row["retrieval_eligible"] is False          # quarantined
            continue
        assert row["runtime_status"] == rec["data_quality"]["runtime_status"] and row["food_name"] == rec["food_name"]
