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
    assert make_fact(engine.library, "NUTR-INDB-ASC205", "fat_g") is None         # kofta, 495 g fat


def test_facts_carry_serving_and_about(engine):
    fact = make_fact(engine.library, "SURVEY-2707713", "carbohydrate_g")          # USDA chapati
    assert fact.text == "about 46.1 g carbohydrate per 100 g"
    assert fact.source == "USDA"
    dev = make_fact(engine.library, "NUTR-INDB-ASC178", "energy_kcal")            # dry aloo
    assert dev.text == "about 109 kcal per bowl" and dev.source != "USDA"


def test_range_only_energy_is_shown_as_a_range(engine):
    lib = engine.library
    rid = next(r for r, rec in lib.records.items()
               if "range only" in ((rec.get("data_quality") or {}).get("usage_constraint") or "").lower()
               and rec.get("energy_estimate_range_per_serving") and lib.flags.get(r, set()) <= {"energy_missing"})
    fact = make_fact(lib, rid, "energy_kcal")
    assert fact is not None and "–" in fact.text and fact.value is None
