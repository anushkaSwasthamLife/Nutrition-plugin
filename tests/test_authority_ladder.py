"""The authority ladder (data/authority_ladder.json, ladder.py): which source wins when two disagree. Advisory until it is signed."""
import copy
import dataclasses
import json

import pytest

from dixit_answer import readiness
from dixit_answer.config import DATA_DIR
from dixit_answer.consistency import find_problems
from dixit_answer.ladder import Ladder, problems

LADDER = json.loads((DATA_DIR / "authority_ladder.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((DATA_DIR / "rules_registry.json").read_text(encoding="utf-8"))


def test_the_real_ladder_fits_the_registry():
    assert problems(LADDER, REGISTRY) == []


def test_every_registry_rule_is_on_a_rung_or_universal():
    ids = {r["id"] for r in REGISTRY["order"]}
    placed = {rule for r in LADDER["rungs"] for rule in r["registry_rules"]} | set(LADDER["universal"])
    assert placed == ids


def test_it_is_a_proposal_and_not_the_dixit_priority():
    assert LADDER["status"] == "PROPOSED" and LADDER["binding"] is False and LADDER["signed_by"] is None
    assert "NOT the Dixit priority integer" in LADDER["note"]
    assert all("priority" not in r for r in LADDER["rungs"])            # rules_registry.json: no priority integers without the base package


@pytest.mark.parametrize("a,b,winner", [
    ("DIXIT-EMERGENCY", "NUT-ALLERGEN-001", "DIXIT-EMERGENCY"),
    ("NUT-GOLDEN-001", "NUT-ALLERGEN-001", "NUT-ALLERGEN-001"),         # what the person told us beats the programme
    ("NUT-LIMIT-001", "NUT-GOLDEN-001", "NUT-LIMIT-001"),               # a limit is never relaxed by a Golden Rule
    ("NUT-RECIPE-001", "NUT-GOLDEN-001", "NUT-GOLDEN-001"),
    ("NUT-SWAP-001", "NUT-LIMIT-001", "NUT-LIMIT-001"),
])
def test_the_stricter_source_is_the_higher_rung(a, b, winner):
    ladder = Ladder(LADDER)
    assert ladder.stricter(a, b) == winner and ladder.stricter(b, a) == winner


def test_same_rung_universal_and_unknown_have_no_winner():
    ladder = Ladder(LADDER)
    assert ladder.stricter("NUT-ALLERGEN-001", "NUT-SAFE-PRE-001") is None     # same rung
    assert ladder.stricter("SEC-INPUT-001", "NUT-GOLDEN-001") is None          # universal: applies on every rung
    assert ladder.stricter("NOPE", "NUT-GOLDEN-001") is None and ladder.rung_of("UNIV-PERSONAL-001") is None


def mutate(fn):
    data = copy.deepcopy(LADDER)
    fn(data)
    return problems(data, REGISTRY)


def test_a_registry_rule_with_no_place_is_reported():
    found = mutate(lambda d: d["rungs"][1]["registry_rules"].remove("NUT-ALLERGEN-001"))
    assert any("NUT-ALLERGEN-001 is on no rung" in p for p in found)


def test_an_unknown_rule_and_a_rule_placed_twice_are_reported():
    assert any("NOPE is not in rules_registry.json" in p for p in mutate(lambda d: d["universal"].append("NOPE")))
    assert any("NUT-GOLDEN-001 appears 2 times" in p for p in mutate(lambda d: d["rungs"][0]["registry_rules"].append("NUT-GOLDEN-001")))


def test_rung_numbers_must_run_from_one_without_gaps():
    assert any("numbered 1 to" in p for p in mutate(lambda d: d["rungs"][2].update(rung=7)))


def test_model_knowledge_must_stay_last():
    def swap(d):
        d["rungs"][-1], d["rungs"][-2] = d["rungs"][-2], d["rungs"][-1]
        for i, r in enumerate(d["rungs"], 1):
            r["rung"] = i
    assert any("must be the last rung" in p for p in mutate(swap))


def test_a_rung_with_no_words_is_reported():
    assert any("rung 4 has no must_never" in p for p in mutate(lambda d: d["rungs"][3].update(must_never=" ")))


def test_a_missing_file_is_reported_not_a_crash():
    assert problems(None, REGISTRY) == ["authority ladder: the file is missing"]


def test_the_startup_check_stops_on_a_broken_ladder(engine):
    broken = copy.deepcopy(LADDER)
    broken["universal"].remove("SEC-INPUT-001")
    found = find_problems(dataclasses.replace(engine.data, authority_ladder=broken), engine.library, engine.index, "development")
    assert any("SEC-INPUT-001 is on no rung" in p for p in found)
    assert not [p for p in find_problems(engine.data, engine.library, engine.index, "development") if "authority ladder" in p]


def test_readiness_lists_the_ladder_as_unsigned_and_the_audit_log_carries_its_version(engine, chat):
    res = readiness.check()
    assert res["files"]["authority_ladder"] == "PROPOSED"
    assert any(i["area"] == "authority_ladder" for i in res["items"])
    chat("what is a good lunch with dal")
    last = [json.loads(line) for line in engine.settings.audit_log.read_text(encoding="utf-8").splitlines() if '"kind": "turn"' in line][-1]
    assert last["versions"]["authority_ladder"] == LADDER["version"]
