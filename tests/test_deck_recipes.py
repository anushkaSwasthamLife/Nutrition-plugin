"""A second programme book (the ADORE 90 Days Challenge recipes) and recipe books in general: books are data only (a manifest entry,
an ingredient map and a recipes file kept outside the repo), dishes are offered by what the person has, and when the person wants
something different from every dish we hold, the AI drafts a different dish that is recalculated and gated. The tests that read
the deck file skip when it is not on this machine."""
import copy
import dataclasses
import json
import shutil

import pytest

from dixit_answer import loader
from dixit_answer.book_recipes import load_book
from dixit_answer.config import DATA_DIR, recipe_book_paths
from dixit_answer.golden import Status
from dixit_answer.llm import MockLLM
from dixit_answer.main_pipeline import MainPipeline
from dixit_answer.person import Person
from dixit_answer.pipeline import Engine
from dixit_answer.recipes import recipe_badge

MAP = json.loads((DATA_DIR / "deck_ingredient_map.json").read_text(encoding="utf-8"))
FOODS = {f["id"]: f for f in json.loads((DATA_DIR / "food_master.json").read_text(encoding="utf-8"))["foods"]}
DECK_FILE = recipe_book_paths()["adore_90_days"]
needs_deck = pytest.mark.skipif(not DECK_FILE.is_file(), reason=f"the deck file is not on this machine ({DECK_FILE})")
VEG = {"allergies": [], "diet_type": "vegetarian"}


def plain(engine, **kw):
    base = {"allergies": [], "diet_type": "vegetarian", "status": Status("non_diabetic"), "doctor_pack": engine.packs.resolve()}
    base.update(kw)
    return Person(**base)


def run_dish(engine, rid, person):
    return engine.recipes.run(person=person, have=[], dish_ids=[rid], unknown_words=[], suggestion=None, wants_list=False, home_foods=[])


# ---------------------------------------------------------------- the map (always checked)
def test_every_map_line_names_known_foods_with_positive_grams():
    for text, entry in MAP["items"].items():
        if entry.get("trace") or entry.get("skip") or entry.get("optional") or entry.get("exclude"):
            continue
        for fid in entry.get("split") or [entry["food"]]:
            assert fid in FOODS, (text, fid)
        assert all(g > 0 for g in [*(entry.get("per") or {}).values(), entry.get("default_g", 1)]), text


def test_every_deck_item_not_offered_as_a_dish_says_why():
    not_dishes = {rid for rid, kind in MAP["kind_overrides"].items() if kind is None}
    assert not_dishes and not_dishes <= set(MAP["kind_override_notes"])


def test_the_books_are_listed_in_the_manifest_only():
    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert [b["id"] for b in manifest["recipe_books"]] == ["healthful_diet", "adore_90_days"]
    assert set(recipe_book_paths({"DIXIT_DECK_RECIPES": "off"})) == {"healthful_diet"}


def test_baking_soda_is_not_a_sweet_drink(engine):
    assert "FOOD-SWEET-DRINKS" not in [m.food_id for m in engine.index.find("add baking soda to the batter")]
    assert [m.food_id for m in engine.index.find("a can of soda")] == ["FOOD-SWEET-DRINKS"]


def test_a_new_ai_draft_differs_from_the_dishes_already_shown(engine):
    person = plain(engine)
    run = lambda avoid: engine.recipes.run(person=person, have=["FOOD-KARELA"], dish_ids=[], unknown_words=[], suggestion=None,  # noqa: E731
                                           wants_list=False, home_foods=[], exclude=["unused"], avoid_drafts=avoid)
    assert run([]).recipe.name == "Karela Sabji (dry)"
    assert run(["Karela Sabji (dry)"]).recipe.name == "Karela Masala"
    out = run(["karela sabji", "Karela Masala", "Karela Bhaji"])          # the same dish under another case or bracket is no new dish
    assert out.recipe is None or out.recipe.origin != "ai_suggested"
    assert any("the same dish as one already shown" in t for t in out.trace)


# ---------------------------------------------------------------- a book added with data only
def test_a_book_added_only_in_data_is_offered(settings, tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    monkeypatch.setattr(loader, "DATA_DIR", data)
    book_map = {"version": "TB-0.1.0", "status": "TEST ONLY", "units": {"g": 1}, "no_amount_units": [],
                "book": {"id": "test_book", "short_title": "Test Book", "title": "Test Book", "citation": "Test Book (2026)",
                         "source_id": "test_book_source"},
                "dish_categories": {"vegetable": "sabji"}, "kind_overrides": {}, "kind_override_notes": {},
                "items": {"palak": {"food": "FOOD-PALAK"}, "onion": {"food": "FOOD-ONION"}, "oil": {"food": "FOOD-OIL"},
                          "salt": {"food": "FOOD-SALT"}}}
    (data / "test_book_map.json").write_text(json.dumps(book_map), encoding="utf-8")
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["recipe_books"].append({"id": "test_book", "map": "test_book_map.json", "file": "test-book.json"})
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    packs = json.loads((data / "doctor_packs.json").read_text(encoding="utf-8"))
    packs["packs"]["DR-DIXIT"]["programmes"]["dixit_lifestyle"]["recipe_books"] = ["test_book"]
    (data / "doctor_packs.json").write_text(json.dumps(packs), encoding="utf-8")
    white = json.loads((data / "white_lists.json").read_text(encoding="utf-8"))
    white["recipe_sources"]["entries"].append({"id": "test_book_source", "label": "Test Book", "allowed_in": ["development"]})
    (data / "white_lists.json").write_text(json.dumps(white), encoding="utf-8")
    recipes = {"_about": {"source": {"title": "Test Book"}}, "recipes": [{
        "id": "TB-01", "name": "Palak Onion Sabji", "category": "vegetable", "book_page": 3,
        "ingredients": [{"item": "palak", "quantity": 250, "unit": "g"}, {"item": "onion", "quantity": 110, "unit": "g"},
                        {"item": "oil", "quantity": 9, "unit": "g"}, {"item": "salt", "quantity": 3, "unit": "g"}],
        "steps": ["Heat the oil and cook the onion for 5 minutes.", "Add the palak and salt and cook for 8 minutes."]}]}
    (tmp_path / "test-book.json").write_text(json.dumps(recipes), encoding="utf-8")
    s = copy.copy(settings)
    s.recipe_books = {"test_book": tmp_path / "test-book.json"}
    engine = Engine(s, llm=MockLLM())
    assert engine.versions["recipe_book:test_book"] == "TB-0.1.0, Test Book, 1 dishes"
    assert engine.books["test_book"].recipes[0]["book"]["credit"] == "Test Book"          # no credit in the map: its short title
    out = run_dish(engine, "TB-01", plain(engine))
    assert out.status == "recipe" and recipe_badge(out.recipe)["text"] == "Recipe from Test Book (test)"


# ---------------------------------------------------------------- the real deck in the engine
@pytest.fixture(scope="module")
def deck_engine(settings):
    if not DECK_FILE.is_file():
        pytest.skip("the deck file is not on this machine")
    s = copy.copy(settings)
    s.recipe_books = {"adore_90_days": DECK_FILE}
    return Engine(s, llm=MockLLM())


@pytest.fixture(scope="module")
def deck_main(deck_engine):
    return MainPipeline(deck_engine)


@needs_deck
def test_every_deck_line_is_in_the_map():
    for raw in load_book(DECK_FILE)["recipes"]:
        for item in raw["ingredients"]:
            assert item["item"] in MAP["items"], (raw["id"], item["item"])


@needs_deck
def test_the_deck_loads_its_dishes(deck_engine):
    summary = deck_engine.books["adore_90_days"].summary()
    assert summary["dishes"] == 62 and summary["components"] == 7
    assert {s["id"] for s in summary["skipped"]} == {"DX-027"}          # its patty is another recipe with no amounts
    assert deck_engine.versions["recipe_book:adore_90_days"] == "DM-0.1.1, 90 Days Challenge, 62 dishes"


@needs_deck
@pytest.mark.parametrize("profile", [{"diet_type": "non_vegetarian", "status": Status("non_diabetic")},
                                     {"diet_type": "vegetarian", "status": Status("type_2")}])
def test_every_deck_dish_passes_for_a_plain_profile_with_usda_numbers(deck_engine, profile):
    person = plain(deck_engine, **profile)
    failing = [(raw["recipe_id"], run_dish(deck_engine, raw["recipe_id"], person).rejected)
               for raw in deck_engine.books["adore_90_days"].recipes
               if run_dish(deck_engine, raw["recipe_id"], person).status != "recipe"]
    assert failing == []


@needs_deck
def test_a_deck_dish_is_credited_to_the_deck_and_names_its_assumed_amounts(deck_main):
    r = deck_main.answer("ragi chilla recipe", None, VEG)
    assert r.badge["kind"] == "programme" and r.badge["text"] == "Recipe from Dr. Dixit's 90 Days Challenge (test)"
    assert "A recipe from Dr. Dixit's 90 Days Challenge, day 81 (transcribed for this app; not yet checked by our dietitian)." in r.answer
    assert "salt 3 g (the book gives no amount)" in r.answer
    assert [g["short"] for g in r.sources if g["key"] == "book"] == ["90 Days Challenge book"]


@needs_deck
def test_the_decks_molasses_is_removed_for_everyone(deck_engine):
    out = run_dish(deck_engine, "DX-035", plain(deck_engine))
    assert out.status == "recipe" and "no molasses" in out.recipe.repairs
    assert recipe_badge(out.recipe)["text"] == "Adapted from Dr. Dixit's 90 Days Challenge (test)"
    assert "FOOD-MOLASSES" not in {i["food"] for i in out.recipe.ingredients}


@needs_deck
def test_a_pack_without_the_deck_never_gets_its_dishes(deck_engine):
    pack = dataclasses.replace(deck_engine.packs.resolve(), recipe_books=frozenset({"healthful_diet"}))
    assert run_dish(deck_engine, "DX-064", plain(deck_engine, doctor_pack=pack)).status != "recipe"


@needs_deck
def test_what_you_have_gets_the_deck_dish_then_different_checked_ai_drafts(deck_main):
    cid, results = None, []
    for message in ("what can I make with karela and curd", "something else", "something different"):
        r = deck_main.answer(message, cid, VEG)
        cid = r.conversation_id
        results.append(r)
    first, second, third = results
    assert first.answer.splitlines()[1] == "- Karela Nafees" and first.badge is None      # a list: the deck dish first
    assert second.badge["kind"] == "ai_checked" and third.badge["kind"] == "ai_checked"
    assert second.answer.split("**")[1] != third.answer.split("**")[1]       # the dish name: a different dish each time
    assert "calculated from USDA" in third.answer
