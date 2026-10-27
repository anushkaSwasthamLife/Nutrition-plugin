"""The Dixit programme's recipe book (Healthful Diet) as offered recipes: conversion, USDA numbers, credit wording, tailoring,
safety of the book's own steps, the similar-dish search and the AI draft for a dish nobody has written."""
import copy
import json

import pytest

from dixit_answer.book_recipes import _quantity, _yield, convert, load_book
from dixit_answer.config import DATA_DIR, DEFAULT_BOOK_RECIPES
from dixit_answer.golden import Status
from dixit_answer.llm import MockLLM
from dixit_answer.main_pipeline import MainPipeline
from dixit_answer.person import Person
from dixit_answer.pipeline import Engine
from dixit_answer.recipes import recipe_badge

MAP = json.loads((DATA_DIR / "book_ingredient_map.json").read_text(encoding="utf-8"))
FOODS = {f["id"]: f for f in json.loads((DATA_DIR / "food_master.json").read_text(encoding="utf-8"))["foods"]}
HAVE_BOOK = DEFAULT_BOOK_RECIPES.is_file()
needs_book = pytest.mark.skipif(not HAVE_BOOK, reason=f"the book file is not on this machine ({DEFAULT_BOOK_RECIPES})")
VEG = {"allergies": [], "diet_type": "vegetarian"}


def mini_book(*recipes):
    return {"_about": {"source": {"title": "Healthful Diet"}}, "recipes": list(recipes)}


def dish(rid="HD-T-01", category="vegetable", ingredients=None, steps=("Cook it.", "Serve it."), **extra):
    base = {"id": rid, "name": "Test sabji", "category": category, "local_names": {"hi": "Test bhaji"}, "book_page": 120,
            "ingredients": ingredients or [{"item": "spinach", "quantity": 1, "unit": "bunch"}], "steps": list(steps),
            "total_yield": "4 vati", "serving_size": "1 vati", "nutrition_per_serving": {"energy_kcal": 99}}
    base.update(extra)
    return base


# ---------------------------------------------------------------- conversion (no book file needed)
def test_amounts_use_the_books_own_units():
    out = convert(mini_book(dish(ingredients=[
        {"item": "spinach", "quantity": 1, "unit": "bunch"},                       # food unit weight: 250 g
        {"item": "chana dal", "quantity": 2, "unit": "vati"},                      # the book: 1 vati = 60 g
        {"item": "oil", "quantity": 2, "unit": "teaspoon"},                        # 4.5 g a teaspoon
        {"item": "green chillies", "quantity": "1-2", "unit": "count"},            # a range: the middle, 5 g each
    ])), MAP, FOODS)
    grams = {i["food"]: i["grams"] for i in out.recipes[0]["ingredients"]}
    assert grams == {"FOOD-PALAK": 250.0, "FOOD-CHANA-DAL": 120.0, "FOOD-OIL": 9.0, "FOOD-GREEN-CHILLI": 7.5}
    units = {i["food"]: i["unit"] for i in out.recipes[0]["ingredients"]}
    assert units["FOOD-CHANA-DAL"] == "2 vati" and units["FOOD-OIL"] == "2 teaspoons" and units["FOOD-PALAK"] == "1 bunch"


def test_open_amounts_are_assumed_and_named_trace_and_optional_items_are_listed_water_is_skipped():
    out = convert(mini_book(dish(ingredients=[
        {"item": "spinach", "quantity": 1, "unit": "bunch"}, {"item": "salt", "quantity": None, "unit": "to taste"},
        {"item": "oil", "quantity": None, "unit": "as needed"}, {"item": "asafetida", "quantity": None, "unit": "pinch"},
        {"item": "fish sauce", "quantity": None, "unit": "optional"}, {"item": "water", "quantity": 2, "unit": "vati"},
    ])), MAP, FOODS)
    r = out.recipes[0]
    assert {a["text"]: (a["grams"], a["book"]) for a in r["assumed"]} == {"salt": (3.0, "to taste"), "oil": (9.0, "as needed")}
    assert r["trace_items"] == ["asafetida"] and r["optional_items"] == ["fish sauce"]
    assert "FOOD-WATER" not in {i["food"] for i in r["ingredients"]}


def test_a_line_naming_several_vegetables_shares_its_grams():
    out = convert(mini_book(dish(ingredients=[{"item": "cut vegetables (carrot, cabbage, capsicum)", "quantity": 1, "unit": "vati"}])),
                  MAP, FOODS)
    assert {i["food"]: i["grams"] for i in out.recipes[0]["ingredients"]} == {"FOOD-CARROT": 20.0, "FOOD-CABBAGE": 20.0, "FOOD-CAPSICUM": 20.0}


@pytest.mark.parametrize("ingredients,reason", [
    ([{"item": "dragon fruit", "quantity": 1, "unit": "count"}], "no mapping"),
    ([{"item": "precooked protein (cooked chicken, paneer, boiled egg, soyabean chunks, etc.)", "quantity": None, "unit": "as needed"}],
     "left to the cook"),
    ([{"item": "paneer", "quantity": None, "unit": "as needed"}], "no assumed amount"),
])
def test_a_dish_that_cannot_be_worked_out_is_skipped_with_the_reason(ingredients, reason):
    out = convert(mini_book(dish(ingredients=ingredients)), MAP, FOODS)
    assert out.recipes == [] and reason in out.skipped[0]["reasons"][0]


def test_components_and_one_step_dishes_are_never_offered():
    out = convert(mini_book(dish("HD-T-02", category="spice_blend"), dish("HD-T-03", steps=("Mix.",))), MAP, FOODS)
    assert out.recipes == [] and out.components[0]["id"] == "HD-T-02" and "two steps" in out.skipped[0]["reasons"][0]


def test_the_recipe_carries_its_source_credit_kind_diet_and_yield():
    r = convert(mini_book(dish(ingredients=[{"item": "minced chicken", "quantity": 500, "unit": "g"}], category="non_vegetarian")),
                MAP, FOODS).recipes[0]
    assert r["source"] == "healthful_diet_book" and r["book"] == {
        "id": "HD-T-01", "page": 120, "title": "Healthful Diet", "signed": False, "source_book": "healthful_diet",
        "citation": "Healthful Diet: Think wise. Eat twice. Drop size (ADORE Trust, 2021)"}
    assert (r["kind"], r["diet"], r["main_ingredients"]) == ("protein", "non_vegetarian", ["FOOD-CHICKEN-MINCE"])
    assert r["yield"] == {"count": 4.0, "unit": "vati", "per_serving": 1.0} and "Test bhaji" in r["aliases"]
    assert "per person" not in r["makes"] and r["printed_nutrition"] == {"energy_kcal": 99}


@pytest.mark.parametrize("made,one,expected", [
    ("5-6 vati", "1 vati", {"count": 5.5, "unit": "vati", "per_serving": 1.0}),
    ("9-10 kababs", "2 kababs", {"count": 9.5, "unit": "kabab", "per_serving": 2.0}),
    ("5 vati", "2 tablespoons", {"count": 5.0, "unit": "vati", "per_serving": None}),
    ("6 no.", "1 thalipeeth (6 inches)", None), ("3 1/2 kg", "2 tablespoons", None),
])
def test_yield_parsing(made, one, expected):
    assert _yield(made, one) == expected


def test_quantity_parsing():
    assert (_quantity(2), _quantity("7-8"), _quantity("0.75-1"), _quantity(None), _quantity(True)) == (2.0, 7.5, 0.875, None, None)


def test_every_book_line_is_in_the_map_and_names_known_foods():
    for text, entry in MAP["items"].items():
        for fid in entry.get("split") or ([entry["food"]] if entry.get("food") else []):
            assert fid in FOODS, (text, fid)


def test_book_only_foods_never_match_chat_text(engine):
    assert "FOOD-CHICKEN" in engine.index.book_only and "FOOD-COCONUT" in engine.index.book_only
    assert [m.food_id for m in engine.index.find("coconut chicken with cashews and mustard seeds")] == []


def test_a_missing_book_file_means_the_book_is_off(tmp_path):
    assert load_book(None) is None and load_book(tmp_path / "nope.json") is None


# ---------------------------------------------------------------- the real book in the engine
@pytest.fixture(scope="module")
def book_engine(settings):
    if not HAVE_BOOK:
        pytest.skip("the book file is not on this machine")
    s = copy.copy(settings)
    s.book_recipes_file = DEFAULT_BOOK_RECIPES
    return Engine(s, llm=MockLLM())


@pytest.fixture(scope="module")
def book_main(book_engine):
    return MainPipeline(book_engine)


def plain(engine, **kw):
    base = {"allergies": [], "diet_type": "non_vegetarian", "status": Status("non_diabetic"), "doctor_pack": engine.packs.resolve()}
    base.update(kw)
    return Person(**base)


def run_dish(engine, rid, person):
    return engine.recipes.run(person=person, have=[], dish_ids=[rid], unknown_words=[], suggestion=None, wants_list=False, home_foods=[])


@needs_book
def test_the_book_loads_its_dishes(book_engine):
    summary = book_engine.book.summary()
    assert summary["dishes"] == 31 and summary["components"] == 22
    assert {s["id"] for s in summary["skipped"]} == {"HD-H-01", "HD-I-01"}
    assert book_engine.versions["book_recipes"] == "Healthful Diet, 31 dishes"


@needs_book
def test_every_book_dish_passes_for_a_plain_profile_with_usda_numbers(book_engine):
    for raw in book_engine.book.recipes:
        out = run_dish(book_engine, raw["recipe_id"], plain(book_engine))
        assert out.status == "recipe" and out.recipe.recipe_id == raw["recipe_id"], (raw["name"], out.rejected)
        assert out.recipe.tier == "programme" and out.recipe.totals.get("energy_kcal", 0) > 0


@needs_book
def test_a_book_dish_is_credited_to_the_book(book_main):
    r = book_main.answer("palak paneer recipe", None, VEG)
    assert r.badge["kind"] == "programme" and r.badge["text"] == "Healthful Diet recipe · Dixit programme (test)"
    assert "A recipe from the Dixit programme's Healthful Diet book, page 127 (transcribed from the book" in r.answer
    assert "Dr. Dixit" not in r.answer                               # the book is credited, never a person
    assert "**Amounts we assumed:** salt 3 g" in r.answer and "kasuri methi" in r.answer
    assert "kcal in each vati" in r.answer and "calculated from USDA" in r.answer
    book = [g for g in r.sources if g["key"] == "book"]
    assert book and book[0]["trusted"] is False and book[0]["records"][0]["id"] == "HD-F-05, page 127"


@needs_book
def test_a_swap_makes_it_a_tailored_version(book_main):
    r = book_main.answer("mix veg khichadi recipe", None, {"allergies": ["milk"], "diet_type": "vegetarian"})
    assert r.badge["kind"] == "adapted" and r.badge["text"].startswith("Tailored from a Healthful Diet recipe")
    assert "A version of a Healthful Diet recipe, page 149, tailored for your health and taste" in r.answer
    assert "ghee" not in r.answer.split("**How:**")[0].lower()


@needs_book
def test_a_food_named_only_in_the_steps_is_still_checked(book_engine):
    out = run_dish(book_engine, "HD-F-05", plain(book_engine, allergies=["gluten"]))       # "Serve hot with roti"
    assert out.status != "recipe"
    assert any("has a step with chapati" in reason for _, reasons in out.rejected for reason in reasons)


@needs_book
def test_a_vegetarian_never_gets_a_meat_dish_from_the_book(book_engine):
    for rid in ("HD-G-01", "HD-G-02", "HD-G-03", "HD-G-04", "HD-G-05"):
        assert run_dish(book_engine, rid, plain(book_engine, diet_type="vegetarian")).status != "recipe"


@needs_book
def test_book_sugar_is_removed_for_everyone(book_engine):
    out = run_dish(book_engine, "HD-F-12", plain(book_engine))
    assert out.status == "recipe" and "no sugar" in out.recipe.repairs and recipe_badge(out.recipe)["kind"] == "adapted"


@needs_book
def test_a_dish_we_dont_hold_gets_the_closest_book_dish(book_main):
    r = book_main.answer("methi paratha recipe", None, VEG)
    assert 'I don\'t have a recipe called "methi paratha" yet' in r.answer and "The closest dish in our recipes is Moringa paratha" in r.answer
    assert r.badge["kind"] == "programme" and any("similar: 'methi paratha'" in t for t in r.audit["recipe_trace"])


@needs_book
def test_similar_needs_the_dish_type_or_a_main_food(book_engine):
    person = plain(book_engine)
    assert book_engine.recipes.similar("chicken curry", person)[0] == "HD-G-01"
    assert book_engine.recipes.similar("chocolate cake", person) == []


class DishModel(MockLLM):
    name, model = "fake", "fake-dish"

    def draft_dish(self, request):
        ids = {f["food_id"] for f in request["available"]}
        assert "FOOD-CHICKEN" not in ids and "FOOD-PANEER" not in ids          # book-only foods and blocked foods are never offered
        return {"name": "Saag", "ingredients": [{"food_id": "FOOD-PALAK", "grams": 250}, {"food_id": "FOOD-ONION", "grams": 110},
                                                {"food_id": "FOOD-OIL", "grams": 9}, {"food_id": "FOOD-SALT", "grams": 3}],
                "steps": ["Heat the oil and cook the onion for 5 minutes.", "Add the palak and salt and cook for 8 minutes."]}


@needs_book
def test_no_similar_dish_means_an_ai_draft_recalculated_and_gated(settings):
    s = copy.copy(settings)
    s.book_recipes_file = DEFAULT_BOOK_RECIPES
    main = MainPipeline(Engine(s, llm=DishModel()))
    r = main.answer("recipe for saag", None, {"allergies": ["milk"], "diet_type": "vegetarian"})
    assert r.badge["kind"] == "ai_checked" and "Our AI drafted one from our food list" in r.answer
    assert "calculated from USDA" in r.answer and any("R5-R8 (named dish): attempt 1: pass" in t for t in r.audit["recipe_trace"])


@needs_book
def test_without_a_model_nothing_is_drafted(book_main):
    r = book_main.answer("recipe for saag", None, VEG)
    assert r.badge is None or r.badge["kind"] != "ai_checked"
    assert any("no model to draft with" in t for t in r.audit["recipe_trace"])


@needs_book
def test_a_list_of_dishes_starts_with_the_programmes_main_dishes(book_main):
    r = book_main.answer("give me a recipe", None, VEG)
    first = r.answer.split("\n")[1].lstrip("- ")
    raw = next(x for x in book_main.engine.book.recipes if x["name"] == first)
    assert raw["kind"] in {"sabji", "dal", "protein"}
