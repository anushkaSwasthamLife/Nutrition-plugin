"""Test reports per level (test_report.py): defined, run and passed are reported separately."""
import copy
import json
import time
from types import SimpleNamespace

import pytest

from dixit_answer import test_report
from dixit_answer.loader import load_manifest


def result(nodeid, when="call"):
    return SimpleNamespace(nodeid=nodeid, when=when)


STATS = {"passed": [result("a"), result("b")], "failed": [], "skipped": [result("c", "setup")]}


@pytest.mark.parametrize("args,scope", [
    ([], "full"), (["tests"], "full"), (["-q", "--no-header", "-p", "no:cacheprovider", "tests"], "full"), (["-W", "ignore", "tests"], "full"), (["-q", "tests"], "full"), (["-q", "tests/"], "full"),
    (["tests/test_release.py"], "partial"), (["-k", "release"], "partial"), (["-m", "slow"], "partial"),
    (["tests/test_loader.py::test_bad_hash_stops_start"], "partial"), (["--lf"], "partial"), (["--deselect", "tests/x.py::t"], "partial"),
])
def test_scope_is_full_only_for_the_whole_suite(args, scope):
    assert test_report.scope_of(args) == scope


def test_a_report_records_the_level_the_environment_and_the_release(tmp_path):
    path = test_report.write_report("development", "development_run", STATS, ["tests"], time.time() - 3, 3, tmp_path)
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["level"] == "development_run" and rec["environment"] == "development" and rec["scope"] == "full"
    assert rec["counts"] == {"passed": 2, "failed": 0, "skipped": 1, "error": 0} and rec["collected"] == 3
    assert rec["result"] == "PASS" and rec["release_id"].startswith("DIXIT-REL-") and "mock" in rec["note"]


def test_a_failure_makes_the_report_a_fail_and_names_the_test(tmp_path):
    stats = {"passed": [result("a")], "failed": [result("tests/t.py::test_bad")]}
    rec = json.loads(test_report.write_report("development", "development_run", stats, ["tests"], time.time(), 2, tmp_path).read_text(encoding="utf-8"))
    assert rec["result"] == "FAIL" and rec["failed"] == ["tests/t.py::test_bad"]


def test_a_run_with_no_passes_is_not_a_pass(tmp_path):
    rec = json.loads(test_report.write_report("development", "development_run", {}, ["tests"], time.time(), 0, tmp_path).read_text(encoding="utf-8"))
    assert rec["result"] == "FAIL"


def rows(tmp_path, manifest=None):
    return {r["level"]: r for r in test_report.summary(tmp_path, manifest)}


def test_with_no_reports_every_run_level_is_not_run_and_never_passed(tmp_path):
    out = rows(tmp_path)
    assert [out[k]["status"] for k in ("defined", "development_run", "staging_run", "soft_launch")] == ["NOT_RUN"] * 4
    assert out["static"]["status"] == "PASS"                 # computed now from the files and the release record


def test_a_development_pass_is_never_shown_as_a_staging_pass(tmp_path):
    test_report.write_report("development", "development_run", STATS, ["tests"], time.time(), 3, tmp_path)
    out = rows(tmp_path)
    assert out["development_run"]["status"] == "PASS" and out["defined"]["status"] == "DEFINED"
    assert out["staging_run"]["status"] == "NOT_RUN" and out["soft_launch"]["status"] == "NOT_RUN"


def test_a_partial_run_is_shown_as_partial_not_pass(tmp_path):
    test_report.write_report("development", "development_run", STATS, ["tests/test_release.py"], time.time(), 3, tmp_path)
    assert rows(tmp_path)["development_run"]["status"] == "PARTIAL"


def test_a_run_on_an_older_release_is_marked(tmp_path):
    test_report.write_report("development", "development_run", STATS, ["tests"], time.time(), 3, tmp_path)
    manifest = copy.deepcopy(load_manifest())
    manifest["release"]["files"]["dish_rules"]["sha256"] = "0" * 64         # a different set of files is a different release ID
    assert "older release" in rows(tmp_path, manifest)["development_run"]["detail"]


def test_static_fails_when_the_files_do_not_match_the_record(tmp_path):
    manifest = copy.deepcopy(load_manifest())
    manifest["release"]["files"]["dish_rules"]["sha256"] = "0" * 64
    assert rows(tmp_path, manifest)["static"]["status"] == "FAIL"


def test_the_newest_report_per_level_wins_and_bad_files_are_ignored(tmp_path):
    (tmp_path / "20200101T000000_development_run_development.json").write_text(
        json.dumps({"level": "development_run", "counts": {"passed": 1, "failed": 1, "skipped": 0, "error": 0}, "result": "FAIL",
                    "scope": "full", "environment": "development", "at": "2020-01-01T00:00:00", "collected": 2}), encoding="utf-8")
    (tmp_path / "junk.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    assert rows(tmp_path)["development_run"]["status"] == "FAIL"
    test_report.write_report("development", "development_run", STATS, ["tests"], time.time(), 3, tmp_path)
    assert rows(tmp_path)["development_run"]["status"] == "PASS"


def test_render_shows_one_line_per_level(tmp_path):
    text = test_report.render(test_report.summary(tmp_path))
    assert [line.split()[0] for line in text.splitlines()] == list(test_report.LEVELS)
