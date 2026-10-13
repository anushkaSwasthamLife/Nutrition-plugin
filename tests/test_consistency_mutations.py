"""Break each data file on purpose and prove the start-up checker names the mistake."""
import copy

import pytest

from dixit_answer.consistency import find_problems


def _problems(engine, mutate):
    data = copy.deepcopy(engine.data)
    mutate(data)
    return find_problems(data, engine.library, engine.index, engine.settings.environment)


def test_real_data_is_clean(engine):
    assert _problems(engine, lambda d: None) == []


MUTATIONS = {
    "recipe unknown food": (lambda d: d.recipes["recipes"][0]["ingredients"].append({"food": "FOOD-NOPE", "grams": 5}), "FOOD-NOPE"),
    "recipe zero grams": (lambda d: d.recipes["recipes"][0]["ingredients"][0].update(grams=0), "bad grams"),
    "recipe per person": (lambda d: d.recipes["recipes"][0].update(makes="1 bowl per person"), "per person"),
    "recipe one step": (lambda d: d.recipes["recipes"][0].update(steps=["only one"]), "fewer than two steps"),
    "recipe duplicate id": (lambda d: d.recipes["recipes"].append(copy.deepcopy(d.recipes["recipes"][0])), "used twice"),
    "approved unchecked": (lambda d: d.approved_list["entries"][0].update(golden_rule_checked=False), "golden_rule_checked"),
    "approved unknown food": (lambda d: d.approved_list["entries"][0]["foods"].append("FOOD-GHOST"), "FOOD-GHOST"),
    "allergen unknown food": (lambda d: next(iter(d.allergen_map["allergens"].values()))["foods"].append("FOOD-GHOST"), "FOOD-GHOST"),
    "major allergen missing": (lambda d: d.allergen_map["major"].append("mustard_x"), "mustard_x"),
    "swap unknown action": (lambda d: d.swap_table["repairs"][0].update(action="delete"), "unknown action"),
    "limits bad margin": (lambda d: d.condition_limits.update(release_margin=1.5), "release_margin"),
    "limits bad max": (lambda d: d.condition_limits["limits"][0].update(daily_max=-1), "bad daily_max"),
    "golden key missing": (lambda d: d.golden_rules.pop("fruit"), "missing 'fruit'"),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_checker_names_each_broken_file(engine, name):
    mutate, expected = MUTATIONS[name]
    found = _problems(engine, mutate)
    assert found and any(expected in p for p in found), (name, found)
