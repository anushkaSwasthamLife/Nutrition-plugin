import copy

import pytest

from dixit_answer.loader import (FLAG_MACRO_MISMATCH, FLAG_SERVING_UNCLEAR, LoadError, load_library, load_manifest,
                                 load_swastham_data)


def test_development_loads_both_sources_with_pinned_counts(engine):
    report = engine.library.report
    assert report["eligible"] == {"usda": 13560, "indian": 865}
    assert set(report["sources"]) == {"usda", "indian"}


def test_quality_gates_catch_the_known_bad_records(engine):
    flags = engine.library.flags
    assert FLAG_MACRO_MISMATCH in flags["SURVEY-2705587"]            # cocoa powder: macros do not add up to 228 kcal (D1); lauki kofta (495 g fat) was corrected in v1.0.5
    assert FLAG_MACRO_MISMATCH not in flags.get("NUTR-INDB-ASC205", set())
    assert FLAG_SERVING_UNCLEAR in flags["NUTR-INDB-ASC096"]         # "4 chapati" at 73 kcal (D2)
    assert FLAG_SERVING_UNCLEAR in flags["NUTR-INDB-ASC144"]         # "4 idli" at 34 kcal (D2)
    assert not engine.library.usable("SURVEY-2705587")


def test_bad_hash_stops_start(settings):
    manifest = copy.deepcopy(load_manifest())
    manifest["library"]["files"]["usda"]["sha256"] = "0" * 64
    with pytest.raises(LoadError, match="Hash mismatch"):
        load_library(settings, manifest)


def test_production_refuses_indian_records_without_a_licence(settings):
    manifest = copy.deepcopy(load_manifest())
    manifest["environments"]["production"]["sources"] = ["usda", "indian"]
    prod = copy.copy(settings)
    prod.environment = "production"
    with pytest.raises(LoadError, match="licence"):
        load_library(prod, manifest)


def test_production_loads_usda_only_and_refuses_test_only_data(settings):
    prod = copy.copy(settings)
    prod.environment = "production"
    library = load_library(prod)
    assert library.sources_on == ["usda"]
    assert not any(library.is_indian(rid) for rid in list(library.records)[:500])
    with pytest.raises(LoadError, match="TEST ONLY|STUB"):
        load_swastham_data(prod)
