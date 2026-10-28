"""The dish search remembers what it is looking for (A) and each part can be taken off (B, the chips on the chat page).

The conversation from the 22 Oct screenshots: "what can i have for protein rich food" -> "but i need less calories" ->
"i need non veg with less calories" used to forget protein and ignore non-veg."""
import re
from pathlib import Path

import pytest

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from tests.edge_helpers import assert_valid

NUTRITION = load_settings().nutrition_dir          # DIXIT_NUTRITION_DIR, as the app reads it
needs_data = pytest.mark.skipif(not (NUTRITION / "indb_source" / "recipes.xlsx").exists() or not list((NUTRITION / "releases").glob("*/alias_table.csv")),
                                reason="INDB files are not on this PC")
NONVEG = {"allergies": ["peanut"], "diet_type": "non_vegetarian", "conditions": ["obesity"]}
VEG = {"allergies": [], "diet_type": "vegetarian"}


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path_factory.mktemp("memory"), dish_lookup=True), llm=MockLLM())


def chat(engine, *messages, profile=NONVEG):
    cid, out = None, []
    for message in messages:
        r = engine.answer(message, cid, profile)
        cid = r.conversation_id
        assert_valid(r, message)
        out.append(r)
    return out


def wishes(r):
    return {(t["field"], t["mode"]) for t in r.plan["targets"]}


def dishes(r):
    return [re.match(r"- (.+?):", l).group(1) for l in r.answer.split("\n") if re.match(r"- .+?: about", l)]


@needs_data
def test_the_screenshot_conversation_keeps_protein_and_reads_non_veg(eng):
    r = chat(eng, "what can i have for protein rich food", "but i need less calories", "i need non veg with less calories")
    assert wishes(r[1]) == {("protein_g", "high"), ("energy_kcal", "low")} and r[1].plan["target_mode"] == "refine"
    assert "high protein and few calories" in r[1].answer
    assert wishes(r[2]) == {("protein_g", "high"), ("energy_kcal", "low")} and r[2].plan["target_diet"] == "non_vegetarian"
    assert r[2].answer.startswith("Non-veg dishes with high protein and few calories") or "non-veg dish" in r[2].answer


@needs_data
@pytest.mark.parametrize("follow", ["but i need less calories", "and low fat", "also less carbs", "less calories too", "now low fat please",
                                    "i need less calories", "with less fat", "same but low carb"])
def test_a_follow_up_wish_is_added_to_the_search(eng, follow):
    r = chat(eng, "high protein food", follow)[1]
    assert r.type == "target_meal_search" and ("protein_g", "high") in wishes(r) and len(wishes(r)) == 2, (follow, wishes(r))


@needs_data
def test_a_wish_for_the_same_nutrient_replaces_it(eng):
    r = chat(eng, "low calorie food", "but high calories")[1]
    assert wishes(r) == {("energy_kcal", "high")}


@needs_data
def test_a_full_new_request_still_starts_a_new_search(eng):
    r = chat(eng, "high protein food", "low carb dishes")[1]
    assert wishes(r) == {("carb_g", "low")} and r.plan["target_mode"] == "new"
    r = chat(eng, "high protein food", "but also low carb dishes")[1]
    assert wishes(r) == {("protein_g", "high"), ("carb_g", "low")}


@needs_data
@pytest.mark.parametrize("messages", [("low calorie non veg food",), ("low calorie food", "non veg please"), ("high protein food", "non veg only")])
def test_non_veg_lists_only_dishes_with_meat_fish_or_egg(eng, messages):
    from dixit_answer.targets import TargetSearch
    r = chat(eng, *messages)[-1]
    assert r.plan["target_diet"] == "non_vegetarian" and dishes(r)
    ts = TargetSearch(eng.composer.dishes, eng.composer.allergy, eng.index)
    codes = {eng.composer.dishes.label(c).split(" (")[0]: c for c in eng.composer.dishes.by_code}
    shown = [d for d in dishes(r) if d in codes]
    assert shown and all(ts.is_nonveg(codes[d]) for d in shown), shown


@needs_data
def test_a_vegetarian_profile_never_gets_meat_when_asking_non_veg(eng):
    r = chat(eng, "high protein food", "non veg please", profile=VEG)[-1]
    from dixit_answer.targets import MEAT_FISH
    assert not any(set(re.findall(r"[a-z]+", d.lower())) & MEAT_FISH for d in dishes(r))


@needs_data
def test_each_part_can_be_taken_off(eng):
    r = chat(eng, "high protein non veg food", "but less calories", "except chicken", "forget without chicken", "forget non veg",
             "forget high protein", "forget calories")
    assert r[2].plan["target_exclude"] and not r[3].plan["target_exclude"]
    assert r[3].plan["target_diet"] == "non_vegetarian" and r[4].plan["target_diet"] == ""
    assert wishes(r[5]) == {("energy_kcal", "low")}
    assert r[6].plan["targets"] == [] and r[6].plan["target_mode"] == "cleared" and "cleared that search" in r[6].answer


@needs_data
def test_forgetting_a_food_brings_back_its_other_names(eng):
    r = chat(eng, "i want a meal with 20 g protein and 300 kcal", "without curd", "forget without curd", profile=VEG)
    assert {"curd", "dahi"} <= set(r[1].plan["target_exclude"]) and r[2].plan["target_exclude"] == []


@needs_data
@pytest.mark.parametrize("reset", ["start over", "new search", "forget it", "clear search", "ok start again please", "reset"])
def test_start_over_clears_the_search(eng, reset):
    r = chat(eng, "high protein food", "but less calories", reset, "less fat")
    assert r[2].plan["target_mode"] == "cleared" and r[2].plan["targets"] == []
    assert ("protein_g", "high") not in wishes(r[3])


@needs_data
def test_forget_with_nothing_to_forget_is_not_a_search_change(eng):
    r = chat(eng, "high protein food", "forget fibre")[1]
    assert not (r.type == "target_meal_search" and r.plan["target_mode"] == "cleared")


@needs_data
def test_the_search_lasts_longer_than_three_turns_but_ends_on_a_new_topic(eng):
    r = chat(eng, "high protein food", "hello", "thanks", "ok", "hi", "but less calories")
    assert wishes(r[-1]) == {("protein_g", "high"), ("energy_kcal", "low")}
    r = chat(eng, "high protein food", "calories in dal", "but less calories")
    assert ("protein_g", "high") not in wishes(r[-1])


def test_chat_page_has_the_search_chips():
    page = (Path(__file__).resolve().parents[1] / "dixit_answer" / "static" / "chat.html").read_text(encoding="utf-8")
    assert 'id="search"' in page and "renderSearch(r)" in page and "'forget without ' + w" in page and 'data-say="start over"' in page


# ---------------------------------------------------------------- "other than this": different ones, never the programme explanation
MORE_PHRASES = ["what can i have other than this", "other than these", "something else", "what other options do i have", "anything other than this",
                "show me different ones", "something different please", "other options", "not these", "different from these"]


@needs_data
@pytest.mark.parametrize("follow", MORE_PHRASES)
def test_a_request_for_different_dishes_continues_the_search(eng, follow):
    r = chat(eng, "high protein non veg food", follow)[1]
    assert r.type == "target_meal_search" and r.plan["target_mode"] == "more" and r.plan["target_exclude"] == [], (follow, r.type)
    assert "programme" not in r.answer.lower().split("\n")[0] and ("protein_g", "high") in wishes(r)


@needs_data
def test_a_bare_what_can_i_have_right_after_a_search_asks_for_more(eng):
    r = chat(eng, "high protein food", "so what can i have")[1]
    assert r.type == "target_meal_search" and r.plan["target_mode"] == "more"
    r = chat(eng, "high protein food", "hello", "hello", "hello", "hello", "what can i have")[-1]     # four turns later it is a new question
    assert r.type != "target_meal_search"


@needs_data
@pytest.mark.parametrize("follow", ["something else", "other than these", "what can i have other than this", "what other options do i have"])
def test_nothing_to_continue_asks_what_the_person_wants(eng, follow):
    r = chat(eng, "hello", follow)[1]
    assert r.type == "more_unclear" and "not sure what to show more of" in r.answer and "I can only help" not in r.answer
    assert chat(eng, follow)[0].type == "more_unclear"


@needs_data
def test_more_after_a_recipe_list_still_skips_what_was_shown(full_engine):
    r = chat(full_engine, "i have aloo and paneer", "what can i have other than this", profile=VEG)
    assert r[1].type == "meal_from_ingredients" and r[1].plan["protocol_level"] != "full"


@needs_data
def test_a_wish_for_more_of_a_nutrient_is_not_a_request_for_other_dishes(eng):
    r = chat(eng, "low calorie food", "more protein")[1]
    assert wishes(r) == {("energy_kcal", "low"), ("protein_g", "high")} and r.plan["target_mode"] == "refine"


# ---------------------------------------------------------------- "i want chicken": a meat or fish wish is a dish search
@needs_data
@pytest.mark.parametrize("message, words", [("i want chicken, what can i have", {"chicken"}), ("i want to eat chicken", {"chicken"}),
                                            ("what can i have with chicken", {"chicken"}), ("i want mutton or fish", {"fish", "mutton"}),
                                            ("fish khana hai kya khau", {"fish"})])
def test_a_meat_or_fish_wish_lists_dishes_with_it(eng, message, words):
    from dixit_answer.hindi import translate
    r = chat(eng, translate(message))[0]
    assert r.type == "target_meal_search" and set(r.plan["target_include"]) == words, (message, r.type)
    assert dishes(r), message                          # the dishes themselves are checked for the meat in test_dishes_for_a_meat_wish_really_contain_it


@needs_data
def test_dishes_for_a_meat_wish_really_contain_it(eng):
    from dixit_answer.targets import TargetSearch
    r = chat(eng, "i want chicken, what can i have")[0]
    ts = TargetSearch(eng.composer.dishes, eng.composer.allergy, eng.index)
    codes = {eng.composer.dishes.label(c).split(" (")[0]: c for c in eng.composer.dishes.by_code}
    for d in dishes(r):
        code = codes[d]
        words = {w.rstrip("s") for w in re.findall(r"[a-z]+", " ".join([eng.composer.dishes.label(code), *eng.composer.dishes.ingredient_names(code)]).lower())}
        assert "chicken" in words, d
        assert ts.is_nonveg(code)


@needs_data
@pytest.mark.parametrize("message", ["can i eat chicken", "is chicken healthy", "should i eat fish", "chicken calories", "is fish good for me"])
def test_a_question_about_a_meat_keeps_its_own_answer(eng, message):
    assert chat(eng, message)[0].type != "target_meal_search"


@needs_data
def test_the_meat_wish_is_part_of_the_search_and_can_be_changed(eng):
    r = chat(eng, "i want chicken, what can i have", "and fish", "except chicken", "forget fish")
    assert set(r[1].plan["target_include"]) == {"chicken", "fish"}
    assert r[2].plan["target_include"] == ["fish"] and "chicken" in r[2].plan["target_exclude"]
    assert r[3].plan["target_include"] == [] and r[3].plan["target_mode"] in {"cleared", "refine"}


@needs_data
def test_a_meat_wish_with_a_calorie_wish_says_when_nothing_counts_as_light(eng):
    r = chat(eng, "i want chicken, what can i have", "but less calories")[1]
    assert wishes(r) == {("energy_kcal", "low")} and r.plan["target_include"] == ["chicken"] and dishes(r)
    first_line = r.answer.split(chr(10))[0]
    assert "counts as few calories" in first_line or "few calories and chicken" in first_line, first_line


@needs_data
def test_a_vegetarian_asking_for_chicken_is_told_why_nothing_is_shown(eng):
    r = chat(eng, "i want chicken, what can i have", profile=VEG)[0]
    assert r.type == "target_meal_search" and not dishes(r) and "vegetarian" in r.answer
