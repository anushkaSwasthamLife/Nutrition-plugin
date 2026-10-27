"""The eval set and its runner (dixit_answer/evals.py, evals/eval_set.json). The pass rate itself is not a test: the eval set holds
cases the rules are known to miss, so it can measure the LLM pipeline against them. What is a test: the rules never miss a critical
case the baseline (evals/baselines/) did not already record as a known miss."""
import copy
import json

import pytest

from dixit_answer import evals
from dixit_answer.main_pipeline import MainPipeline


@pytest.fixture(scope="module")
def target(full_engine):
    return evals.RulesTarget(MainPipeline(full_engine))


def test_the_eval_set_has_no_mistakes():
    assert evals.problems(evals.load()) == []


def test_every_safety_label_has_cases():
    data = evals.load()
    used = {s for c in data["conversations"] for t in c["turns"]
            for s in (t["expect"].get("safety") if isinstance(t["expect"].get("safety"), list) else [t["expect"].get("safety")])}
    assert set(data["safety_labels"]) <= used


def test_every_safety_case_is_critical():
    data = evals.load()
    for conv in data["conversations"]:
        for turn in conv["turns"]:
            safety = turn["expect"].get("safety")
            if safety not in (None, "none") and conv["area"] not in {"routing"}:
                assert turn["expect"].get("critical"), conv["id"]


def test_mistakes_in_the_eval_set_are_found():
    data = copy.deepcopy(evals.load())
    data["conversations"].append(copy.deepcopy(data["conversations"][0]))
    turn = data["conversations"][1]["turns"][0]
    turn["expect"].update({"safety": "heart", "kind": "poem", "must_not_match": ["("], "colour": "red"})
    data["conversations"][2]["profile"] = "astronaut"
    found = "\n".join(evals.problems(data))
    for bit in ("duplicate id", "unknown safety label 'heart'", "unknown kind 'poem'", "bad pattern", "unknown expectation 'colour'",
                "unknown profile 'astronaut'"):
        assert bit in found


def test_checks():
    obs = evals.Observation("A bowl of poha has about 161 calories.", "none", "plugin", "nutrition_fact")
    expect = {"safety": "none", "handled_by": "plugin", "kind": "nutrition_fact", "must_contain": ["161"], "must_contain_any": ["kcal", "calories"],
              "must_not_contain": ["peanut"], "must_not_match": [r"\d+\s*g\b"], "rubric": "is right"}
    checks = evals.check_turn(expect, obs)
    assert all(c["passed"] for c in checks if c["check"] != "rubric")
    assert [c["passed"] for c in checks if c["check"] == "rubric"] == [None]          # not judged is never a pass
    wrong = evals.check_turn({"safety": ["self_harm", "starving"], "must_contain": ["112"]}, obs)
    assert [c["passed"] for c in wrong] == [False, False]


def test_a_run_reports_safety_and_areas(target):
    report = evals.run(evals.load(), target, ids={"EM-01", "NE-01", "RC-02", "RT-01"})
    s = report["summary"]
    assert s["turns"] == 5 and s["passed"] == 5 and s["critical_failed"] == []
    assert s["safety_recall"]["emergency_heart_stroke"] == {"cases": 1, "caught": 1}
    em = next(t for t in report["turns"] if t["id"] == "EM-01.1")
    assert em["observed"]["safety"] == "emergency_heart_stroke" and em["observed"]["kind"] == "emergency"


def test_a_critical_miss_fails_the_run(target):
    data = copy.deepcopy(evals.load())
    data["conversations"] = [c for c in data["conversations"] if c["id"] == "RT-01"]
    data["conversations"][0]["turns"][0]["expect"].update({"must_contain": ["no such words"], "critical": True})
    assert evals.run(data, target)["summary"]["critical_failed"] == ["RT-01.1"]


def test_the_rules_miss_no_critical_case_beyond_the_baselines_known_misses(target):
    baseline = json.loads((evals.EVAL_SET.parent / "baselines" / "rules-EV-0.1.0.json").read_text(encoding="utf-8"))
    report = evals.run(evals.load(), target)
    new = set(report["summary"]["critical_failed"]) - set(baseline["summary"]["critical_failed"])
    assert not new, f"new critical misses: {sorted(new)}"
