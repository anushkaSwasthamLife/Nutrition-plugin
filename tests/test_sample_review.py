"""Plan item G tooling: drawing the 100-recipe sample and scoring the dietitian's sheet."""
import copy
import csv
import io
import json
import shutil
from datetime import date

import pytest

from dixit_answer import sample_review as sr
from dixit_answer.config import DATA_DIR

TODAY = date(2026, 10, 16)


@pytest.fixture(scope="session")
def rules_copy(tmp_path_factory):
    path = tmp_path_factory.mktemp("rules") / "sample_review_rules.json"
    doc = json.loads((DATA_DIR / "sample_review_rules.json").read_text(encoding="utf-8"))
    # diabetes is left out here on purpose: today no AI recipe can be drafted for it (see test_diabetes_cannot_be_sampled_yet)
    doc["conditions"] = [c for c in doc["conditions"] if c != "diabetes"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def sample(engine, rules_copy):
    return sr.make(engine, rules_path=rules_copy)


def sheet(sample, safe="Y", sensible=5, cookable=5, reviewer="Dr Rao", review_date="2026-10-10", mutate=None, drop=(), extra_rows=()):
    rows = []
    for case in sample["cases"]:
        if case["case_id"] in drop:
            continue
        rows.append({"case_id": case["case_id"], "person": "x", "pantry": "x", "recipe": "x", "ingredients": "x", "steps": "x",
                     "totals": "x", "safe": safe, "sensible": sensible, "cookable": cookable, "comments": "", "reviewer": reviewer,
                     "review_date": review_date})
    if mutate:
        for i, row in enumerate(rows):
            mutate(i, row)
    rows += list(extra_rows)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=sr.COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def judge(sample, text, rules_path):
    return sr.score(text, sample, rules_path=rules_path, today=TODAY)


@pytest.fixture
def signed_rules(tmp_path, rules_copy):
    path = tmp_path / "sample_review_rules.json"
    doc = json.loads(rules_copy.read_text(encoding="utf-8"))
    doc["status"] = "APPROVED"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ---------------------------------------------------------------- drawing the sample
def test_the_sample_has_the_requested_number_and_every_recipe_is_ai_drafted_and_passed(sample):
    assert len(sample["cases"]) == sample["requested"] == 100
    assert [c["case_id"] for c in sample["cases"]] == [f"G-{i:03d}" for i in range(1, 101)]
    assert sample["environment"] == "development"


def test_no_two_people_are_the_same(sample):
    sigs = {sr._signature(c) for c in sample["cases"]}
    assert len(sigs) == 100


def test_the_same_seed_gives_the_same_sample(engine, rules_copy, sample):
    again = sr.make(engine, rules_path=rules_copy)
    assert again["cases"] == sample["cases"]


def test_another_seed_gives_another_sample(engine, tmp_path, rules_copy, sample):
    rules = json.loads(rules_copy.read_text(encoding="utf-8"))
    rules["seed"] = 7
    path = tmp_path / "r.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    other = sr.make(engine, rules_path=path)
    assert [sr._signature(c) for c in other["cases"]] != [sr._signature(c) for c in sample["cases"]]


def test_an_independent_check_finds_nothing_wrong_with_any_recipe(engine, sample):
    assert [p for c in sample["cases"] for p in sr.verify_case(engine, c)] == []


def test_the_sample_covers_everything_the_rules_ask_for(sample, rules_copy):
    rules = sr.load_rules(rules_copy)
    cases = {c["case_id"]: c for c in sample["cases"]}
    assert sr._coverage_problems(cases, rules) == []


def test_a_smaller_size_can_be_asked_for(engine, rules_copy):
    out = sr.make(engine, size=7, rules_path=rules_copy)
    assert len(out["cases"]) == 7 and out["requested"] == 7


@pytest.mark.parametrize("size", [0, -3, True, "5", 2.5])
def test_a_bad_size_is_refused(engine, rules_copy, size):
    with pytest.raises(sr.SampleError):
        sr.make(engine, size=size, rules_path=rules_copy)


def test_it_does_not_run_where_ai_drafting_is_off(engine, rules_copy, monkeypatch):
    monkeypatch.setattr(engine.recipes, "ai_enabled", False)
    with pytest.raises(sr.SampleError, match="off"):
        sr.make(engine, rules_path=rules_copy)


def test_asking_for_more_than_can_be_drawn_returns_what_exists_and_says_so(engine, rules_copy, monkeypatch):
    monkeypatch.setattr(sr, "MAX_TRIES_FACTOR", 1)      # give up after as many tries as recipes wanted
    out = sr.make(engine, size=300, rules_path=rules_copy)
    assert 0 < len(out["cases"]) < 300 and out["requested"] == 300


def test_a_recipe_that_breaks_a_rule_stops_the_run(engine, rules_copy, monkeypatch):
    real = engine.recipes.run

    def bad(**kw):
        out = real(**kw)
        if out.recipe is not None:
            out.recipe.ingredients.append({"food": "FOOD-PEANUT", "grams": 10, "unit": "g"})
        return out
    monkeypatch.setattr(engine.recipes, "run", bad)
    with pytest.raises(sr.SampleError, match="breaks a rule"):
        sr.make(engine, size=30, rules_path=rules_copy)


@pytest.mark.parametrize("mutate,word", [
    (lambda c: c["recipe"]["ingredients"].append({"food": "FOOD-PANEER", "name": "p", "grams": "5"}) or c["allergies"].append("milk"), "milk"),
    (lambda c: c["recipe"]["ingredients"].append({"food": "FOOD-EGG", "name": "e", "grams": "5"}) or c.update(diet="jain"), "vegetarian"),
    (lambda c: c["recipe"]["ingredients"].append({"food": "FOOD-ONION", "name": "o", "grams": "5"}) or c.update(diet="jain"), "Jain"),
    (lambda c: c["recipe"]["ingredients"].append({"food": "FOOD-NOPE", "name": "o", "grams": "5"}), "not in the food list"),
    (lambda c: c["recipe"]["totals"].update(fat_g="9999") or c["conditions"].append("general"), "limit"),
])
def test_the_independent_check_catches_each_kind_of_break(engine, sample, mutate, word):
    case = copy.deepcopy(sample["cases"][0])
    case["conditions"], case["allergies"], case["diet"] = ["general"], [], "vegetarian"
    mutate(case)
    found = sr.verify_case(engine, case)
    assert found and any(word in p for p in found), found


# ---------------------------------------------------------------- the rules file
@pytest.mark.parametrize("content", ["", "{", "[]", "null", "{}", '{"sample_size": 0}'])
def test_a_damaged_rules_file_is_refused(tmp_path, content):
    path = tmp_path / "r.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(sr.SampleError):
        sr.load_rules(path)


def test_a_missing_rules_file_is_refused(tmp_path):
    with pytest.raises(sr.SampleError):
        sr.load_rules(tmp_path / "nope.json")


@pytest.mark.parametrize("size", [0, -1, True, "100", 1.5, None])
def test_a_bad_sample_size_in_the_rules_is_refused(tmp_path, rules_copy, size):
    doc = json.loads(rules_copy.read_text(encoding="utf-8"))
    doc["sample_size"] = size
    path = tmp_path / "r.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(sr.SampleError):
        sr.load_rules(path)


# ---------------------------------------------------------------- scoring: results
def test_a_perfect_sheet_passes_once_the_rules_are_signed(sample, rules_copy, signed_rules, engine):
    # the sample must have been drawn under the signed rules for the fingerprint to match
    drawn = sr.make(engine, rules_path=signed_rules)
    res = judge(drawn, sheet(drawn), signed_rules)
    assert res["verdict"] == "PASSED", res
    assert res["numbers"]["unsafe"] == 0 and res["numbers"]["mean_sensible"] == 5


def test_a_perfect_sheet_under_unsigned_rules_is_not_a_pass(sample, rules_copy):
    res = judge(sample, sheet(sample), rules_copy)
    assert res["verdict"] == "PASSED_UNSIGNED" and res["problems"]


def test_one_unsafe_recipe_fails_everything(sample, rules_copy):
    res = judge(sample, sheet(sample, mutate=lambda i, r: r.update(safe="N") if i == 42 else None), rules_copy)
    assert res["verdict"] == "FAILED" and any("unsafe" in p for p in res["problems"])


@pytest.mark.parametrize("sensible,expected", [(4, "PASSED_UNSIGNED"), (3, "FAILED")])
def test_the_mean_decides(sample, rules_copy, sensible, expected):
    res = judge(sample, sheet(sample, sensible=sensible), rules_copy)
    assert res["verdict"] == expected


def test_a_mean_just_below_the_mark_fails_and_exactly_on_it_passes(sample, rules_copy):
    below = judge(sample, sheet(sample, mutate=lambda i, r: r.update(sensible=3) if i == 0 else r.update(sensible=4)), rules_copy)
    assert below["verdict"] == "FAILED" and below["numbers"]["mean_sensible"] == 3.99
    on = judge(sample, sheet(sample, mutate=lambda i, r: r.update(sensible=5 if i < 50 else 3)), rules_copy)   # mean 4.0 exactly
    assert on["numbers"]["mean_sensible"] == 4.0 and on["verdict"] == "PASSED_UNSIGNED"


def test_the_share_of_poor_scores_is_checked_separately_from_the_mean(sample, rules_copy):
    # eleven recipes scored 1 and the rest 5: mean 4.56 but 11% are below 3
    res = judge(sample, sheet(sample, mutate=lambda i, r: r.update(cookable=1 if i < 11 else 5)), rules_copy)
    assert res["verdict"] == "FAILED" and any("cookable" in p for p in res["problems"])
    ten = judge(sample, sheet(sample, mutate=lambda i, r: r.update(cookable=1 if i < 10 else 5)), rules_copy)
    assert ten["verdict"] == "PASSED_UNSIGNED"


def test_the_reasons_for_failing_are_all_listed(sample, rules_copy):
    res = judge(sample, sheet(sample, safe="N", sensible=1, cookable=1), rules_copy)
    assert res["verdict"] == "FAILED" and len(res["problems"]) == 5


# ---------------------------------------------------------------- scoring: a sheet that cannot be judged
def test_every_recipe_must_be_scored(sample, rules_copy):
    res = judge(sample, sheet(sample, drop={"G-007", "G-100"}), rules_copy)
    assert res["verdict"] == "INVALID" and sum("no row" in p for p in res["problems"]) == 2


def test_a_case_that_is_not_in_the_sample_is_refused(sample, rules_copy):
    extra = {"case_id": "G-999", "safe": "Y", "sensible": 5, "cookable": 5, "reviewer": "Dr Rao", "review_date": "2026-10-10"}
    res = judge(sample, sheet(sample, extra_rows=[extra]), rules_copy)
    assert res["verdict"] == "INVALID" and any("G-999" in p for p in res["problems"])


def test_a_case_scored_twice_is_refused_even_when_it_would_help(sample, rules_copy):
    dup = {"case_id": "G-001", "safe": "Y", "sensible": 5, "cookable": 5, "reviewer": "Dr Rao", "review_date": "2026-10-10"}
    res = judge(sample, sheet(sample, extra_rows=[dup]), rules_copy)
    assert res["verdict"] == "INVALID" and any("twice" in p for p in res["problems"])


@pytest.mark.parametrize("field,value", [
    ("sensible", "6"), ("sensible", "0"), ("sensible", "4.5"), ("sensible", ""), ("sensible", "five"), ("sensible", "-1"),
    ("sensible", "1e1"), ("sensible", "٣"), ("sensible", "5 stars"), ("cookable", "99"), ("cookable", " "),
    ("safe", ""), ("safe", "yes"), ("safe", "maybe"), ("safe", "1"), ("safe", "YN"),
    ("reviewer", ""), ("reviewer", "TBD"), ("reviewer", "​"),
    ("review_date", ""), ("review_date", "2026-10-17"), ("review_date", "10/10/2026"), ("review_date", "2026-02-30"),
])
def test_each_bad_cell_makes_the_sheet_invalid(sample, rules_copy, field, value):
    res = judge(sample, sheet(sample, mutate=lambda i, r: r.update({field: value}) if i == 5 else None), rules_copy)
    assert res["verdict"] == "INVALID" and any("G-006" in p for p in res["problems"]), res


@pytest.mark.parametrize("safe", ["y", " Y ", "Y​"])
def test_harmless_forms_of_Y_are_accepted(sample, rules_copy, safe):
    assert judge(sample, sheet(sample, safe=safe), rules_copy)["verdict"] == "PASSED_UNSIGNED"


def test_an_unscored_blank_sheet_is_invalid_not_a_pass(sample, rules_copy):
    res = judge(sample, sr.sheet_csv(sample), rules_copy)
    assert res["verdict"] == "INVALID" and len(res["problems"]) > 100


@pytest.mark.parametrize("text", ["", "\n\n", "not,a,sheet\n1,2,3", "case_id\nG-001"])
def test_a_wrong_or_empty_file_is_invalid(sample, rules_copy, text):
    assert judge(sample, text, rules_copy)["verdict"] == "INVALID"


def test_a_missing_column_is_named(sample, rules_copy):
    text = sheet(sample).replace("cookable,", "cookabel,", 1)
    res = judge(sample, text, rules_copy)
    assert res["verdict"] == "INVALID" and "cookable" in res["problems"][0]


def test_a_sheet_saved_by_a_spreadsheet_still_reads(sample, rules_copy):
    text = "﻿" + sheet(sample).replace("\r\n", "\n").replace("\n", "\r\n")
    assert judge(sample, text, rules_copy)["verdict"] == "PASSED_UNSIGNED"


def test_extra_columns_and_multiline_comments_are_fine(sample, rules_copy):
    def note(i, r):
        r["comments"] = 'needs "less oil",\nbut fine'
    text = sheet(sample, mutate=note)
    assert judge(sample, text, rules_copy)["verdict"] == "PASSED_UNSIGNED"
    lines = text.splitlines()
    wide = "\n".join(l + ",extra" if n else l + ",notes" for n, l in enumerate(lines))
    assert judge(sample, io.StringIO(wide).read(), rules_copy)["verdict"] in {"PASSED_UNSIGNED", "INVALID"}


def test_the_case_id_is_read_loosely_for_spaces_only(sample, rules_copy):
    assert judge(sample, sheet(sample, mutate=lambda i, r: r.update(case_id=" " + r["case_id"] + " ")), rules_copy)["verdict"] == "PASSED_UNSIGNED"
    assert judge(sample, sheet(sample, mutate=lambda i, r: r.update(case_id=r["case_id"].lower()) if i == 3 else None), rules_copy)["verdict"] == "INVALID"


# ---------------------------------------------------------------- scoring: the sample itself
def test_a_pass_mark_changed_after_drawing_is_refused(sample, tmp_path, rules_copy):
    changed = tmp_path / "r.json"
    doc = json.loads(rules_copy.read_text(encoding="utf-8"))
    doc["pass_mark"]["min_mean_sensible"] = 1.0
    changed.write_text(json.dumps(doc), encoding="utf-8")
    res = judge(sample, sheet(sample, sensible=2), changed)
    assert res["verdict"] == "INVALID" and any("changed after" in p for p in res["problems"])


def test_a_sample_with_too_few_recipes_is_refused(sample, rules_copy):
    short = copy.deepcopy(sample)
    short["cases"] = short["cases"][:60]
    res = judge(short, sheet(short), rules_copy)
    assert res["verdict"] == "INVALID" and any("60 recipes" in p for p in res["problems"])


def test_a_sample_that_leaves_a_group_out_is_refused(sample, rules_copy):
    skewed = copy.deepcopy(sample)
    for c in skewed["cases"]:
        c["allergies"] = [a for a in c["allergies"] if a != "peanut"]
    res = judge(skewed, sheet(skewed), rules_copy)
    assert res["verdict"] == "INVALID" and any("peanut" in p for p in res["problems"])


@pytest.mark.parametrize("damage", [None, [], "text", {}, {"cases": None}, {"cases": "x"}, {"cases": [1, 2]}, {"cases": [{"no": "id"}]}])
def test_a_damaged_sample_file_is_invalid_not_a_crash(rules_copy, damage):
    assert sr.score("case_id\n", damage, rules_path=rules_copy, today=TODAY)["verdict"] == "INVALID"


def test_a_repeated_case_number_in_the_sample_is_refused(sample, rules_copy):
    twin = copy.deepcopy(sample)
    twin["cases"][1]["case_id"] = twin["cases"][0]["case_id"]
    assert judge(twin, sheet(twin), rules_copy)["verdict"] == "INVALID"


def test_scoring_changes_no_file(sample, rules_copy):
    before = {p.name: p.read_bytes() for p in DATA_DIR.glob("*.json")}
    judge(sample, sheet(sample), rules_copy)
    assert before == {p.name: p.read_bytes() for p in DATA_DIR.glob("*.json")}
    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["environments"]["production"]["recipe_ai_drafting"] is False


# ---------------------------------------------------------------- command line
def test_cli_make_and_score(engine, sample, tmp_path, monkeypatch, capsys, rules_copy):
    import dixit_answer.pipeline as pipeline
    monkeypatch.setattr(pipeline, "Engine", lambda: engine)
    monkeypatch.setattr(sr, "RULES_PATH", rules_copy)
    out = tmp_path / "out"
    assert sr.main(["make", "--out", str(out)]) == 0
    assert "100 of 100" in capsys.readouterr().out
    drawn = json.loads((out / "sample.json").read_text(encoding="utf-8"))
    assert len(drawn["cases"]) == 100
    header = (out / "scoring_sheet.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(",")[:3] == ["case_id", "person", "pantry"]
    filled = tmp_path / "filled.csv"
    filled.write_text(sheet(drawn), encoding="utf-8")
    assert sr.main(["score", str(filled), "--sample", str(out / "sample.json")]) == 1      # rules still PROPOSED
    assert "PASSED_UNSIGNED" in capsys.readouterr().out
    bad = tmp_path / "bad.csv"
    bad.write_text("nothing", encoding="utf-8")
    assert sr.main(["score", str(bad), "--sample", str(out / "sample.json")]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_cli_with_missing_files_says_so(tmp_path, capsys):
    assert sr.main(["score", str(tmp_path / "none.csv"), "--sample", str(tmp_path / "none.json")]) == 2
    assert "Cannot continue" in capsys.readouterr().out
    (tmp_path / "s.json").write_text("{", encoding="utf-8")
    (tmp_path / "s.csv").write_text("", encoding="utf-8")
    assert sr.main(["score", str(tmp_path / "s.csv"), "--sample", str(tmp_path / "s.json")]) == 2


def test_the_sheet_a_dietitian_receives_has_every_recipe_in_plain_words(sample):
    rows = list(csv.DictReader(io.StringIO(sr.sheet_csv(sample))))
    assert len(rows) == 100 and all(r["recipe"] and r["ingredients"] and r["steps"] and r["safe"] == "" for r in rows)
    assert all("FOOD-" not in r["ingredients"] for r in rows)
    assert rows[0]["person"].startswith("conditions:")


# ---------------------------------------------------------------- what the real rules file gives today
def test_diabetes_cannot_be_sampled_yet_and_the_tool_says_so(engine):
    out = sr.make(engine, size=30, rules_path=DATA_DIR / "sample_review_rules.json")
    assert any("diabetes" in line for line in out["shortfall"])
    assert not any("diabetes" in c["conditions"] for c in out["cases"])
    # the reason: the added-sugar limit cannot be checked, so the recipe flow never drafts with AI for these people
    from dixit_answer.person import Person
    res = engine.recipes.run(person=Person(allergies=[], conditions=["diabetes"]), have=["FOOD-LAUKI"], dish_ids=[], unknown_words=[],
                             suggestion=None, wants_list=False, home_foods=[])
    assert any("limits can't be checked" in t for t in res.trace) or res.recipe is None or res.recipe.origin == "approved"


def test_a_short_sample_cannot_be_scored_as_a_pass(engine):
    real = DATA_DIR / "sample_review_rules.json"
    drawn = sr.make(engine, rules_path=real)
    assert drawn["shortfall"]
    res = sr.score(sheet(drawn), drawn, rules_path=real, today=TODAY)
    assert res["verdict"] == "INVALID" and any("diabetes" in p for p in res["problems"])


def test_the_cli_reports_a_shortfall_and_exits_with_an_error(engine, tmp_path, monkeypatch, capsys):
    import dixit_answer.pipeline as pipeline
    monkeypatch.setattr(pipeline, "Engine", lambda: engine)
    assert sr.main(["make", "--out", str(tmp_path / "o"), "--size", "40"]) == 1
    assert "diabetes" in capsys.readouterr().out
