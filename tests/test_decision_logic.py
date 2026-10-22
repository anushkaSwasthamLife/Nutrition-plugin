"""The client's Golden Rules KB, sheet 'Decision Logic': a DRAFT reference (baseline + modifier per topic), and the one rule it
already changes today, fruit for diabetic and prediabetic people (golden_rules.json, GR-PARTIAL-0.3)."""
import copy
import json
import shutil

import pytest

import dixit_answer.loader as loader
from dixit_answer.config import DATA_DIR
from dixit_answer.golden import Status
from dixit_answer.loader import LoadError, load_manifest, load_swastham_data
from dixit_answer.readiness import check

FILE = DATA_DIR / "decision_logic_draft.json"
DL = json.loads(FILE.read_text(encoding="utf-8"))
GOLDEN = json.loads((DATA_DIR / "golden_rules.json").read_text(encoding="utf-8"))


def failed(result):
    return [c for c in result.checks if not c["passed"]]


@pytest.fixture
def ask(engine):
    def run(message, profile=None, cid=None):
        return engine.answer(message, cid, profile if profile is not None else {"allergies": []})
    return run


def topics():
    return {t["id"]: t for t in DL["topics"]}


# ---------------------------------------------------------------- registered, draft, unsigned
def test_file_is_registered_and_a_draft():
    assert DL["status"] == "DRAFT"
    assert load_manifest()["swastham_data"]["decision_logic"] == "decision_logic_draft.json"


def test_readiness_lists_it_as_unsigned():
    problems = [i for i in check()["items"] if i["area"] == "decision_logic"]
    assert problems and "DRAFT" in problems[0]["problem"]


def test_production_refuses_a_draft_file_even_when_everything_else_is_signed(tmp_path, settings, monkeypatch):
    shutil.copytree(DATA_DIR, tmp_path / "data")
    manifest = json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8"))
    for key, name in manifest["swastham_data"].items():
        if key == "decision_logic":
            continue
        doc = json.loads((tmp_path / "data" / name).read_text(encoding="utf-8"))
        doc["status"] = "APPROVED"
        (tmp_path / "data" / name).write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path / "data")
    prod = copy.copy(settings)
    prod.environment = "production"
    with pytest.raises(LoadError, match="decision_logic_draft.json"):
        load_swastham_data(prod, manifest)


def test_development_loads_it(settings):
    assert load_swastham_data(settings).decision_logic["version"] == DL["version"]


# ---------------------------------------------------------------- the content
def test_four_topics_each_with_a_baseline_modifiers_and_open_questions():
    assert list(topics()) == ["DL-SODIUM", "DL-PROTEIN", "DL-OIL", "DL-FRUIT"]
    codes = []
    for t in DL["topics"]:
        assert t["baseline"]["summary"].strip() and t["modifiers"] and t["precedence"]["rationale"].strip()
        assert t["kb_text"]["baseline"].strip() and t["kb_text"]["modifiers"].strip(), "the sheet's own words are kept beside our summary"
        assert t["open_questions"], t["id"]
        codes += [q["code"] for q in t["open_questions"]]
    assert len(codes) == len(set(codes)) == 15


def test_every_modifier_has_a_known_direction_and_none_relaxes_a_golden_rule():
    allowed = set(DL["direction_values"])
    for t in DL["topics"]:
        for m in t["modifiers"]:
            assert m["effect"] in allowed and m["effect"] != "relax", (t["id"], m)
            assert m["applies_to"] in {"amount", "food_choice"} and m["condition"].strip() and m["summary"].strip()
    assert DL["authority"]["rank"] == 3 and "never relax" in DL["authority"]["meaning"]


def test_the_sheet_s_directions_are_kept():
    t = topics()
    sodium = {m["condition"]: m["effect"] for m in t["DL-SODIUM"]["modifiers"]}
    assert sodium == {"hypertension": "tighten", "ckd": "review"}
    protein = {m["condition"]: m["effect"] for m in t["DL-PROTEIN"]["modifiers"]}
    assert protein == {"ckd": "reverse", "hyperuricemia": "redirect"}          # CKD flips the direction; uric acid changes the food, not the amount
    assert t["DL-PROTEIN"]["baseline"]["numeric"] == {"g_per_kg_per_day": 0.83, "role": "floor"}
    assert t["DL-OIL"]["modifiers"][0]["condition"] == "dyslipidemia"
    assert t["DL-FRUIT"]["modifiers"][0]["effect"] == "no_exception"


def test_nothing_without_a_number_is_enforced():
    assert DL["enforcement"].startswith("none")
    for t in DL["topics"]:
        for m in t["modifiers"]:
            assert m["numeric"] is None, (t["id"], "a modifier gained a number: it needs a signed source before it is enforced")


def test_the_draft_does_not_touch_the_recipe_limits():
    limits = json.loads((DATA_DIR / "condition_limits_test.json").read_text(encoding="utf-8"))
    assert not any(r["condition"] in {"ckd", "hypertension", "dyslipidemia", "hyperuricemia"} and r.get("source") not in (None, "PLACEHOLDER")
                   for r in limits["limits"])


# ---------------------------------------------------------------- the fruit rule it changes today
def test_fruit_rule_in_golden_rules_is_filled_for_diabetic_and_prediabetic_and_marked_draft():
    assert GOLDEN["version"] == "GR-PARTIAL-0.3" and GOLDEN["status"] == "PARTIAL"
    for status in ("diabetic", "prediabetic"):
        entry = GOLDEN["fruit"][status]
        assert entry["allowed"] == "no" and entry["status"] == "DRAFT" and entry["source"].strip()
        assert "fruit rule" in entry["text"] and "no fruit and no sweets" in entry["text"]
        assert "Dixit expert" in entry["text"]                       # a change needs clearance
    assert "golden_rules_kb" in GOLDEN["sources"]
    impl = topics()["DL-FRUIT"]["implemented_in"]
    assert impl["file"] == "golden_rules.json" and impl["version"] == GOLDEN["version"]


def test_the_other_fruit_cases_are_unchanged():
    f = GOLDEN["fruit"]
    assert f["non_diabetic"]["allowed"] == "within_meal_window"
    assert f["currently_non_diabetic_unvalidated"]["allowed"] == "no"
    assert f["currently_non_diabetic_validated"] == {"allowed": "unknown", "text": None}      # reintroduction wording: DL-14, still open
    assert f["unknown"]["allowed"] == "depends_on_status"


def test_fruit_text_makes_no_relaxing_claim(engine):
    g = engine.services.golden
    for status in ("diabetic", "prediabetic"):
        assert g.relaxing_claims(g.fruit_position(Status(status)).text) == []


@pytest.mark.parametrize("status", ["diabetic", "prediabetic"])
def test_a_mango_question_states_the_rule_and_says_the_facts_are_not_permission(ask, status):
    r = ask("Can I eat a mango?", {"allergies": [], "diabetes_status": status})
    assert "no fruit and no sweets" in r.answer and "not permission" in r.answer
    assert "you can" not in r.answer.lower() and not failed(r)


def test_recipes_still_leave_fruit_out_for_diabetic_and_prediabetic(ask):
    for status in ("diabetic", "prediabetic"):
        r = ask("I have mango, onion and aloo, what can I make?", {"allergies": [], "diabetes_status": status})
        assert "I left out mango because of the programme's fruit rule" in r.answer and not failed(r)
