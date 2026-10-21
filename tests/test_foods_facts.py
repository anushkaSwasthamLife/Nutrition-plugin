import copy

from dixit_answer.facts import make_fact
from dixit_answer.foods import LibrarySearch


def test_dal_never_matches_medallion(engine):
    hits = LibrarySearch(engine.library).search("dal", limit=50)
    assert hits
    assert not any("medallion" in h["food_name"].lower() for h in hits)


def test_food_index_prefers_longest_alias_and_spelling_variants(engine):
    find = lambda text: [m.food_id for m in engine.index.find(text)]
    assert find("sweet potato sabzi") == ["FOOD-SWEET-POTATO"]
    assert find("phool gobhi and alu") == ["FOOD-CAULIFLOWER", "FOOD-ALOO"]
    assert find("I like medallions") == []


def test_no_fact_from_unclear_serving_or_impossible_values(engine):
    assert make_fact(engine.library, "NUTR-INDB-ASC096", "energy_kcal") is None   # chapati, serving unclear
    assert make_fact(engine.library, "SURVEY-2705587", "energy_kcal") is None     # cocoa powder, macros do not add up to the energy
    assert make_fact(engine.library, "NUTR-INDB-ASC205", "fat_g").value == 31.76  # kofta: 495 g fat before the v1.0.5 frying-oil correction


def test_facts_carry_serving_and_about(engine):
    fact = make_fact(engine.library, "SURVEY-2707713", "carbohydrate_g")          # USDA chapati
    assert fact.text == "about 46.1 g of carbohydrate per 100 g"
    assert fact.source == "USDA"
    dev = make_fact(engine.library, "NUTR-INDB-ASC178", "energy_kcal")            # dry aloo
    assert dev.text == "about 109 calories per bowl" and dev.source != "USDA"


def test_range_only_energy_is_shown_as_a_range(engine):
    lib = engine.library
    rid = "NUTR-INDB-BFP386"                         # v1.0.5 no longer has a "range only" record, so mark one as it used to be
    rec = lib.records[rid]
    saved, saved_flags = copy.deepcopy(rec["data_quality"]), set(lib.flags.get(rid, set()))
    rec["data_quality"]["usage_constraint"] = "Energy as EST_LOW-EST_HIGH range only; never quote a point value"
    try:
        lib.flags[rid] = set()
        fact = make_fact(lib, rid, "energy_kcal")
    finally:
        rec["data_quality"] = saved
        lib.flags[rid] = saved_flags
    assert fact is not None and "–" in fact.text and fact.value is None
