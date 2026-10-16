"""Plan item D: unknown-item queue and dietitian review."""
import json
import shutil
import sqlite3

import pytest

from dixit_answer.config import DATA_DIR
from dixit_answer.foods import FoodIndex, LibrarySearch
from dixit_answer.review_queue import ApplyError, QueueError, ReviewQueue


def line(**kw):
    return json.dumps({"at": "2026-10-16T10:00:00", "conversation_id": "c1", "turn": 1, **kw}) + "\n"


@pytest.fixture
def env(tmp_path, engine):
    for name in ("food_master.json", "allergen_map.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")
    q = ReviewQueue(tmp_path / "queue.sqlite3", audit)

    class E:
        queue, audit_path, dir, engine_ = q, audit, tmp_path, engine
        index = FoodIndex(engine.data.food_master, engine.words["spelling_map"])
        fm, al = tmp_path / "food_master.json", tmp_path / "allergen_map.json"

        def add(self, text):
            with open(audit, "a", encoding="utf-8") as h:
                h.write(text)

        def kale(self):
            hits = [h for h in LibrarySearch(engine.library).search("kale raw", 10) if not engine.library.is_indian(h["record_id"])]
            return hits[0]["record_id"]
    return E()


def test_sync_reads_new_and_old_style_lines_once(env):
    env.add(line(kind="unknown_item", word="Kasuri Methi", item_kind="ingredient", suggested_food_id=""))
    env.add(line(kind="unknown_item", word="kasuri methi", item_kind="ingredient", suggested_food_id="", turn=2))
    env.add(line(kind="turn", turn=3, recipe_trace=["R1: unknown ['moringa', 'kasuri methi'], left out and queued for review"], plan={}))
    env.add(line(kind="turn", turn=4, recipe_trace=["R1: 'palak paner' unknown, asking about Palak"], plan={"unknown_dish": "kothimbir vadi"}))
    env.add(line(kind="unknown_item", word="kothimbir vadi", item_kind="dish", suggested_food_id="", turn=4))   # same turn as the old-style line
    assert env.queue.sync() == 6
    assert env.queue.sync() == 0
    items = {(i["word"], i["kind"]): i for i in env.queue.items()}
    assert items[("kasuri methi", "ingredient")]["count"] == 3          # three different turns
    assert ("moringa", "ingredient") in items and ("palak paner", "ingredient") in items
    assert items[("kothimbir vadi", "dish")]["count"] == 1               # two lines for the same turn count once
    assert env.queue.items()[0]["word"] == "kasuri methi"                # most asked first


def test_sync_is_incremental_and_ignores_half_written_and_bad_lines(env):
    env.add(line(kind="unknown_item", word="moringa", item_kind="ingredient", suggested_food_id=""))
    env.add("{broken\n")
    env.add(line(kind="unknown_item", word="half", item_kind="ingredient", suggested_food_id="")[:-1])   # no newline yet
    assert env.queue.sync() == 1
    env.add("\n")                                                        # the line is now complete
    assert env.queue.sync() == 1
    assert {i["word"] for i in env.queue.items()} == {"moringa", "half"}


def test_decide_checks_everything_it_needs(env):
    env.add(line(kind="unknown_item", word="kale", item_kind="ingredient", suggested_food_id=""))
    env.add(line(kind="unknown_item", word="kothimbir vadi", item_kind="dish", suggested_food_id=""))
    env.queue.sync()
    lib, cats, alg = env.engine_.library, {"vegetable", "pantry"}, {"milk", "gluten"}

    def decide(word, kind, action, **f):
        return env.queue.decide(word, kind, action, f.pop("by", "Dr Rao"), env.index, lib, alg, cats, **f)
    with pytest.raises(QueueError, match="who is deciding"):
        decide("kale", "ingredient", "alias", by="", food_id="FOOD-ALOO")
    with pytest.raises(QueueError, match="not an option"):
        decide("kale", "ingredient", "plan")
    with pytest.raises(QueueError, match="not an option"):
        decide("kothimbir vadi", "dish", "alias", food_id="FOOD-ALOO")
    with pytest.raises(QueueError, match="Choose which food"):
        decide("kale", "ingredient", "alias", food_id="FOOD-NOPE")
    with pytest.raises(QueueError, match="reason"):
        decide("kale", "ingredient", "reject")
    with pytest.raises(QueueError, match="not in the queue"):
        decide("nothing here", "ingredient", "reject", reason="not a food")
    base = dict(name="kale", category="vegetable", allergens=[], allergens_confirmed=True, record_id=env.kale())
    for bad, msg in [({"category": "dessert"}, "category"), ({"allergens_confirmed": False}, "Confirm the allergens"),
                     ({"allergens": ["lava"]}, "not one we track"), ({"record_id": "NOPE-1"}, "USDA record"),
                     ({"name": "aloo"}, "already have a food")]:
        with pytest.raises(QueueError, match=msg):
            decide("kale", "ingredient", "new_food", **{**base, **bad})
    indian = next(r for r in lib.records if lib.is_indian(r))
    with pytest.raises(QueueError, match="Indian"):
        decide("kale", "ingredient", "new_food", **{**base, "record_id": indian})
    assert decide("kale", "ingredient", "new_food", **base)["status"] == "decided"
    assert decide("kothimbir vadi", "dish", "plan", note="Maharashtrian snack")["status"] == "planned"
    env.queue.reopen("kale", "ingredient")
    assert env.queue.items(kind="ingredient")[0]["status"] == "open"


def test_apply_alias_and_new_food_with_allergen(env):
    for w, k in (("lady finger fry", "ingredient"), ("kale", "ingredient"), ("khoa", "ingredient")):
        env.add(line(kind="unknown_item", word=w, item_kind=k, suggested_food_id="FOOD-BHINDI" if w.startswith("lady") else ""))
    env.queue.sync()
    lib, cats, alg = env.engine_.library, {"vegetable", "dairy"}, {"milk", "gluten"}
    env.queue.decide("lady finger fry", "ingredient", "alias", "Dr Rao", env.index, lib, alg, cats, food_id="FOOD-BHINDI")
    env.queue.decide("kale", "ingredient", "new_food", "Dr Rao", env.index, lib, alg, cats, name="kale", category="vegetable",
                     allergens=[], allergens_confirmed=True, record_id=env.kale(), aliases=["kale leaves"])
    env.queue.decide("khoa", "ingredient", "new_food", "Dr Rao", env.index, lib, alg, cats, name="khoa", category="dairy",
                     allergens=["milk"], allergens_confirmed=True, record_id=env.kale())
    result = env.queue.apply(env.engine_, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 3 and result["version"] == "FM-0.3.1" and not result["skipped"]
    fm, al = json.loads(env.fm.read_text(encoding="utf-8")), json.loads(env.al.read_text(encoding="utf-8"))
    assert "lady finger fry" in next(f for f in fm["foods"] if f["id"] == "FOOD-BHINDI")["aliases"]
    kale = next(f for f in fm["foods"] if f["id"] == "FOOD-KALE")
    assert kale["aliases"] == ["kale", "kale leaves"] and kale["ingredient_record"] == env.kale()
    assert "FOOD-KHOA" in al["allergens"]["milk"]["foods"] and al["version"] != env.engine_.data.allergen_map["version"]
    assert len(fm["review_log"]) == 3 and fm["review_log"][0]["applied_by"] == "Dr Rao"
    new_index = FoodIndex(fm, env.engine_.words["spelling_map"])
    assert [m.food_id for m in new_index.find("I have lady finger fry and kale leaves")] == ["FOOD-BHINDI", "FOOD-KALE"]
    assert {i["status"] for i in env.queue.items()} == {"applied"} and env.queue.items()[0]["applied_in"] == "FM-0.3.1"
    assert env.queue.apply(env.engine_, env.fm, env.al, "Dr Rao")["applied"] == 0           # nothing left to apply
    with pytest.raises(QueueError, match="already been applied"):
        env.queue.decide("kale", "ingredient", "reject", "Dr Rao", env.index, lib, alg, cats, reason="changed my mind")


def test_a_bad_item_is_skipped_with_its_reason_and_writes_nothing(env):
    env.add(line(kind="unknown_item", word="kale", item_kind="ingredient", suggested_food_id=""))
    env.queue.sync()
    env.queue.decide("kale", "ingredient", "new_food", "Dr Rao", env.index, env.engine_.library, {"milk"}, {"vegetable"},
                     name="kale", category="vegetable", allergens=[], allergens_confirmed=True, record_id=env.kale())
    with sqlite3.connect(env.queue.db_path) as con:                      # a decision that went bad after it was made
        con.execute("UPDATE items SET decision=? WHERE word='kale'", (json.dumps(
            {"action": "new_food", "name": "kale", "category": "vegetable", "allergens": [], "record_id": "NOT-A-RECORD", "aliases": []}),))
    before = (env.fm.read_text(encoding="utf-8"), env.al.read_text(encoding="utf-8"))
    result = env.queue.apply(env.engine_, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 0 and "NOT-A-RECORD" in result["skipped"][0]["why"]
    assert (env.fm.read_text(encoding="utf-8"), env.al.read_text(encoding="utf-8")) == before
    item = env.queue.items()[0]
    assert item["status"] == "decided" and "NOT-A-RECORD" in item["decision"]["not_applied_because"]


def test_apply_skips_a_name_taken_in_the_meantime(env):
    env.add(line(kind="unknown_item", word="okra fry", item_kind="ingredient", suggested_food_id=""))
    env.queue.sync()
    env.queue.decide("okra fry", "ingredient", "alias", "Dr Rao", env.index, env.engine_.library, set(), set(), food_id="FOOD-BHINDI")
    fm = json.loads(env.fm.read_text(encoding="utf-8"))
    next(f for f in fm["foods"] if f["id"] == "FOOD-ALOO")["aliases"].append("okra fry")        # someone else used the name
    env.fm.write_text(json.dumps(fm), encoding="utf-8")
    result = env.queue.apply(env.engine_, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 0 and "already used" in result["skipped"][0]["why"]


def test_the_pipeline_writes_unknown_item_records(engine, chat):
    chat("I have aloo and xyzzyfruit, what can I cook?", "can I get a recipe for kothimbir vadi")
    records = [json.loads(x) for x in engine.settings.audit_log.read_text(encoding="utf-8").splitlines()]
    words = {(r["word"], r["item_kind"]) for r in records if r.get("kind") == "unknown_item"}
    assert ("kothimbir vadi", "dish") in words
