"""Lookup ranking: identity first, then record quality; ties between different dishes stay ambiguous (N14)."""
from dixit_answer.foods import LibrarySearch
from dixit_answer.loader import (FLAG_ENERGY_MISSING, FLAG_MACRO_MISMATCH, FLAG_MACROS_INCOMPLETE,
                                 FLAG_SERVING_UNCLEAR, Library)


def rec(name, confidence=None, record_type="prepared_food", basis="source_published"):
    quality = {"energy_basis": basis}
    if confidence is not None:
        quality["calorie_confidence_pct"] = confidence
    return {"food_name": name, "record_type": record_type, "data_quality": quality}


def lib(records, flags=None):
    return Library(environment="development", records=records, flags=flags or {})


def test_same_dish_prefers_complete_then_more_confident():
    library = lib({"A": rec("Idli", 60), "B": rec("Idli", 90), "C": rec("Idli", 95)},
                  flags={"C": {FLAG_SERVING_UNCLEAR}})
    result = LibrarySearch(library).resolve("idli")
    assert result["status"] == "found" and result["record_id"] == "B"


def test_incomplete_record_loses_even_with_higher_confidence():
    for flag in (FLAG_ENERGY_MISSING, FLAG_MACROS_INCOMPLETE, FLAG_SERVING_UNCLEAR):
        library = lib({"A": rec("Dosa", 99), "B": rec("Dosa", 10)}, flags={"A": {flag}})
        assert LibrarySearch(library).resolve("dosa")["record_id"] == "B"


def test_quality_never_breaks_a_tie_between_different_dishes():
    library = lib({"A": rec("Dal tadka", 99), "B": rec("Dal fry", 10)})
    result = LibrarySearch(library).resolve("dal")
    assert result["status"] == "ambiguous"


def test_identity_beats_quality():
    library = lib({"A": rec("Rice", 10), "B": rec("Rice pudding", 99)})
    assert LibrarySearch(library).resolve("rice")["record_id"] == "A"


def test_prepared_dish_beats_ingredient_even_if_less_confident():
    library = lib({"A": rec("Dal", 50, "prepared_food"), "B": rec("Dal", 99, "ingredient")})
    assert LibrarySearch(library).resolve("dal")["record_id"] == "A"


def test_odd_confidence_values_do_not_crash_or_win():
    for bad in (None, "high", -5, 250, True, float("nan"), [90]):
        library = lib({"A": rec("Poha", bad), "B": rec("Poha", 70)})
        assert LibrarySearch(library).resolve("poha")["record_id"] == "B", bad


def test_missing_data_quality_block():
    library = lib({"A": {"food_name": "Upma", "record_type": "prepared_food"}, "B": rec("Upma", 80)})
    assert LibrarySearch(library).resolve("upma")["record_id"] == "B"


def test_macro_mismatch_record_is_never_returned():
    library = lib({"A": rec("Kofta", 99), "B": rec("Kofta", 10)}, flags={"A": {FLAG_MACRO_MISMATCH}})
    assert LibrarySearch(library).resolve("kofta")["record_id"] == "B"


def test_deterministic_for_identical_records():
    library = lib({"A": rec("Roti", 80), "B": rec("Roti", 80)})
    first = LibrarySearch(library).resolve("roti")
    assert all(LibrarySearch(library).resolve("roti") == first for _ in range(5))


def test_empty_and_unknown_queries():
    search = LibrarySearch(lib({"A": rec("Roti", 80)}))
    assert search.resolve("")["status"] == "not_found"
    assert search.resolve("   ")["status"] == "not_found"
    assert search.resolve("zzzz")["status"] == "not_found"
