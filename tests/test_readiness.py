"""Plan item F: production refuses limits without a source, reviewer and date; the readiness report lists what is unsigned."""
import copy
import json
import shutil
from datetime import date

import pytest

from dixit_answer import readiness
from dixit_answer.config import DATA_DIR
from dixit_answer.consistency import find_problems
from dixit_answer.sign_off import limit_problems

TODAY = date(2026, 10, 16)


def row(**kw):
    base = {"condition": "general", "nutrient": "fat_g", "daily_max": 70, "source": "ICMR-NIN 2020 table 4",
            "reviewer": "Dr Rao", "review_date": "2026-10-01"}
    base.update(kw)
    return base


def probs(*rows):
    return limit_problems({"limits": list(rows)}, TODAY)


def test_a_fully_signed_row_passes():
    assert probs(row()) == []


@pytest.mark.parametrize("field,value,word", [
    ("source", "PLACEHOLDER", "source"), ("source", "placeholder - to confirm", "source"), ("source", "", "source"),
    ("source", "   ", "source"), ("source", None, "source"), ("source", 12, "source"), ("source", "TBD", "source"),
    ("source", "N/A", "source"), ("source", "TEST ONLY numbers", "source"), ("source", ["x"], "source"),
    ("reviewer", None, "reviewer"), ("reviewer", "", "reviewer"), ("reviewer", " \t", "reviewer"),
    ("reviewer", "none", "reviewer"), ("reviewer", 0, "reviewer"), ("reviewer", False, "reviewer"),
    ("review_date", None, "review_date"), ("review_date", "", "review_date"), ("review_date", "16/10/2026", "not a date"),
    ("review_date", "2026-13-01", "not a date"), ("review_date", "2026-02-30", "not a date"), ("review_date", "2025-02-29", "not a date"),
    ("review_date", "2026-10-17", "future"), ("review_date", "2999-01-01", "future"), ("review_date", 20261001, "missing"),
    ("review_date", "2026-10-01T10:00:00", "not a date"), ("review_date", "yesterday", "not a date"),
])
def test_each_bad_field_is_named(field, value, word):
    found = probs(row(**{field: value}))
    assert found and any(word in p for p in found), found


def test_a_missing_field_is_refused():
    for field in ("source", "reviewer", "review_date"):
        r = row()
        del r[field]
        assert probs(r), field


def test_leap_day_and_today_are_fine():
    assert probs(row(review_date="2024-02-29")) == []
    assert probs(row(review_date="2026-10-16")) == []
    assert probs(row(review_date=" 2026-10-16 ")) == []


def test_a_normal_source_passes():
    assert probs(row(source="Indian Council of Medical Research, 2024")) == []


def test_duplicate_condition_nutrient_is_flagged_once_per_pair():
    found = probs(row(), row(), row())
    assert len([p for p in found if "twice" in p]) == 1
    assert probs(row(), row(nutrient="energy_kcal"), row(condition="obesity")) == []


def test_every_row_is_reported_not_only_the_first():
    found = probs(row(source="PLACEHOLDER"), row(nutrient="energy_kcal", reviewer=None), row(nutrient="sodium_mg", review_date=None))
    assert len(found) == 3


@pytest.mark.parametrize("cfg", [{}, {"limits": []}, {"limits": None}, {"limits": "no"}, {"limits": {"a": 1}}])
def test_no_table_is_a_problem(cfg):
    assert limit_problems(cfg, TODAY)


@pytest.mark.parametrize("bad", [None, 5, "text", [], ["a"]])
def test_a_row_that_is_not_a_row_does_not_crash(bad):
    found = probs(row(), bad)
    assert any("is not a row" in p for p in found)


# ---- start-up check
def test_development_ignores_provenance_but_production_applies_it(engine):
    data = engine.data
    assert find_problems(data, engine.library, engine.index, "development") == []
    prod = find_problems(data, engine.library, engine.index, "production")
    assert any("no real source" in p for p in prod) and any("no reviewer" in p for p in prod)


def _signed(engine):
    data = copy.deepcopy(engine.data)
    for r in data.condition_limits["limits"]:
        r.update(source="Real source 2026", reviewer="Dr Rao", review_date="2026-10-01")
    return data


def test_signed_limits_pass_the_startup_check(engine):
    found = find_problems(_signed(engine), engine.library, engine.index, "production")
    assert not [p for p in found if p.startswith("limits")]


def test_one_unsigned_row_is_enough(engine):
    data = _signed(engine)
    data.condition_limits["limits"][3]["reviewer"] = None
    found = find_problems(data, engine.library, engine.index, "production")
    assert sum("has no reviewer" in p for p in found) == 1


# ---- readiness report
@pytest.fixture
def signed_copy(tmp_path):
    """A copy of the data folder with every file signed and every limit filled in."""
    d = tmp_path / "data"
    shutil.copytree(DATA_DIR, d)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    for key, name in manifest["swastham_data"].items():
        doc = json.loads((d / name).read_text(encoding="utf-8"))
        doc["status"] = "APPROVED"
        if key in ("approved_list", "recipes"):
            doc["approved_by"], doc["approved_on"] = "Dr Rao", "2026-10-01"
        if key == "condition_limits":
            for r in doc["limits"]:
                r.update(source="Real source 2026", reviewer="Dr Rao", review_date="2026-10-01")
        if key == "white_lists":
            for section in ("recipe_sources", "cooking_methods", "units"):
                for e in doc[section]["entries"]:
                    e.update(approved_by="Dr Rao", approved_on="2026-10-01")
            foods = json.loads((d / manifest["swastham_data"]["food_master"]).read_text(encoding="utf-8"))["foods"]
            doc["ingredient_aliases"]["entries"] = [{"food_id": f["id"], "alias": a, "approved_by": "Dr Rao", "approved_on": "2026-10-01"}
                                                    for f in foods for a in [f["name"], *f.get("aliases", [])]]
        if key == "black_lists":
            for section in ("blocked_records", "allergen_derivatives", "bad_mappings"):
                for e in doc[section]["entries"]:
                    e.update(reviewer="Dr Rao", review_date="2026-10-01")
            doc["unsafe_ingredients"].update(reviewed_empty_by="Dr Rao", reviewed_empty_on="2026-10-01")
        (d / name).write_text(json.dumps(doc), encoding="utf-8")
    return d


def run(d):
    return readiness.check(d / "manifest.json", d)


def _edit(d, name, fn):
    doc = json.loads((d / name).read_text(encoding="utf-8"))
    fn(doc)
    (d / name).write_text(json.dumps(doc), encoding="utf-8")


def test_the_real_data_is_not_ready_and_says_why():
    res = readiness.check()
    assert not res["ready"]
    text = readiness.render(res)
    assert "NOT READY" in text and "condition_limits" in text and "PLACEHOLDER" in text
    assert res["files"]["emergency_rules"] == "PROPOSED"


def test_a_fully_signed_copy_is_ready(signed_copy):
    res = run(signed_copy)
    assert res["ready"], res["items"]
    text = readiness.render(res)
    assert "READY" in text and "NOT" not in text


def test_nothing_in_the_real_folder_changed():
    before = {p.name: p.read_bytes() for p in DATA_DIR.glob("*.json")}
    readiness.check()
    assert before == {p.name: p.read_bytes() for p in DATA_DIR.glob("*.json")}


@pytest.mark.parametrize("status", ["TEST ONLY", "test only", " Test Only ", "STUB", "PARTIAL", "PROPOSED", "PLACEHOLDER", "DRAFT", "", None])
def test_every_unsigned_status_blocks(signed_copy, status):
    _edit(signed_copy, "emergency_rules.json", lambda doc: doc.update(status=status))
    assert not run(signed_copy)["ready"]


def test_missing_status_blocks(signed_copy):
    _edit(signed_copy, "emergency_rules.json", lambda doc: doc.pop("status"))
    assert not run(signed_copy)["ready"]


def test_approver_fields_are_needed(signed_copy):
    _edit(signed_copy, "recipes_test.json", lambda doc: doc.update(approved_by=""))
    res = run(signed_copy)
    assert not res["ready"] and any("approved_by" in i["problem"] for i in res["items"])
    _edit(signed_copy, "recipes_test.json", lambda doc: doc.update(approved_by="Dr Rao", approved_on=None))
    assert any("approved_on" in i["problem"] for i in run(signed_copy)["items"])


def test_one_limit_without_reviewer_blocks(signed_copy):
    _edit(signed_copy, "condition_limits_test.json", lambda doc: doc["limits"][2].update(reviewer=None))
    assert not run(signed_copy)["ready"]


def test_a_missing_file_is_named_not_a_crash(signed_copy):
    (signed_copy / "word_lists.json").unlink()
    res = run(signed_copy)
    assert not res["ready"] and any(i["area"] == "word_lists" for i in res["items"])
    assert res["files"]["word_lists"] == "unreadable"


@pytest.mark.parametrize("content", ["", "not json", "[]", "null", "12", '"text"', "{"])
def test_a_damaged_file_is_named_not_a_crash(signed_copy, content):
    (signed_copy / "golden_rules.json").write_text(content, encoding="utf-8")
    res = run(signed_copy)
    assert not res["ready"] and any(i["area"] == "golden_rules" for i in res["items"])


@pytest.mark.parametrize("content", ["", "nope", "[]", '{"swastham_data": []}', '{"swastham_data": {}}', "{}"])
def test_a_damaged_manifest_is_not_ready(signed_copy, content):
    (signed_copy / "manifest.json").write_text(content, encoding="utf-8")
    res = run(signed_copy)
    assert not res["ready"] and res["items"]


def test_missing_manifest_is_not_ready(tmp_path):
    assert not readiness.check(tmp_path / "none.json", tmp_path)["ready"]


def test_production_switches_are_checked(signed_copy):
    def flip(doc):
        doc["environments"]["production"]["recipe_ai_drafting"] = True
    _edit(signed_copy, "manifest.json", flip)
    assert any("AI recipe drafting" in i["problem"] for i in run(signed_copy)["items"])

    def indian(doc):
        doc["environments"]["production"].update(recipe_ai_drafting=False, sources=["usda", "indian"], indian_licence_id=None)
    _edit(signed_copy, "manifest.json", indian)
    assert any("licence" in i["problem"] for i in run(signed_copy)["items"])

    def test_only(doc):
        doc["environments"]["production"].update(sources=["usda"], allow_test_only_data=True)
    _edit(signed_copy, "manifest.json", test_only)
    assert any("test-only" in i["problem"] for i in run(signed_copy)["items"])

    def none(doc):
        del doc["environments"]["production"]
    _edit(signed_copy, "manifest.json", none)
    assert any("no production" in i["problem"] for i in run(signed_copy)["items"])


def test_cli_exit_codes_and_json(capsys):
    assert readiness.main([]) == 1
    assert "NOT READY" in capsys.readouterr().out
    assert readiness.main(["--json"]) == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["ready"] is False and parsed["items"]


def test_cli_ready_returns_zero(signed_copy, monkeypatch, capsys):
    real = readiness.check
    monkeypatch.setattr(readiness, "check", lambda: real(signed_copy / "manifest.json", signed_copy))
    assert readiness.main([]) == 0
    assert "READY" in capsys.readouterr().out


# ---- second round of edge cases: look-alike and invisible text, date forms
@pytest.mark.parametrize("field,value", [
    ("source", "​"), ("source", "PLACE​HOLDER"), ("source", "ＰＬＡＣＥＨＯＬＤＥＲ"), ("source", "--"), ("source", "x"),
    ("source", "ab"), ("source", "‍⁠"), ("reviewer", "​‍"), ("reviewer", "\xa0"), ("reviewer", "TBC"),
    ("reviewer", "."), ("reviewer", "x"), ("reviewer", True),
])
def test_invisible_and_stand_in_text_is_not_a_signature(field, value):
    assert probs(row(**{field: value}))


@pytest.mark.parametrize("value", ["٢٠٢٦-١٠-٠١", "2026-1-5", "2026/10/01", "2026.10.01", "20261001", "2026-10-1", "0001-01-01",
                                   "1999-12-31", "2026-10-01 10:00", "2026-10-01\n2026-10-02", "-2026-10-01"])
def test_only_a_plain_recent_date_is_accepted(value):
    assert probs(row(review_date=value))


@pytest.mark.parametrize("value", ["2026-10-01", "2000-01-01", "2026-10-01​", "２０２６-１０-０１"])
def test_plain_dates_and_their_invisible_or_full_width_forms_are_read_as_the_same_date(value):
    assert probs(row(review_date=value)) == []


def test_names_in_other_scripts_are_fine():
    assert probs(row(reviewer="डॉ. राव", source="भारतीय आयुर्विज्ञान अनुसंधान परिषद 2024")) == []


def test_duplicate_detection_ignores_case_and_spaces():
    assert any("twice" in p for p in probs(row(), row(condition=" GENERAL ")))


@pytest.mark.parametrize("status", ["approved (draft)", "​TEST ONLY", "ＴＥＳＴ ＯＮＬＹ", "Pending review", "unsigned", "Proposed by A",
                                    "None", "null", ["TEST ONLY"], {"a": 1}, 0, False])
def test_odd_statuses_are_still_not_signed(status):
    from dixit_answer.sign_off import file_problems
    assert file_problems("f.json", {"status": status})


@pytest.mark.parametrize("status", ["APPROVED", "Signed by Dr Rao 2026-10-01", "SIGNED OFF"])
def test_a_signed_status_passes(status):
    from dixit_answer.sign_off import file_problems
    assert file_problems("f.json", {"status": status}) == []


def test_a_console_that_cannot_show_a_character_does_not_crash_the_report(signed_copy, monkeypatch, tmp_path):
    import subprocess, sys
    _edit(signed_copy, "condition_limits_test.json", lambda doc: doc["limits"][0].update(source="डॉ"))
    res = run(signed_copy)
    assert not res["ready"]
    out = subprocess.run([sys.executable, "-m", "dixit_answer.readiness"], capture_output=True,
                         env={**__import__("os").environ, "PYTHONIOENCODING": "cp1252"}, cwd=str(DATA_DIR.parent.parent))
    assert out.returncode == 1 and b"NOT READY" in out.stdout
