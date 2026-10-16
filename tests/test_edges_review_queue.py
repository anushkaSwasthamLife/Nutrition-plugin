"""Edge cases for the unknown-item queue and dietitian review (plan item D)."""
import json
import os
import shutil
import sqlite3
import stat
import threading
import time

import pytest

from dixit_answer import loader
from dixit_answer.config import DATA_DIR
from dixit_answer.foods import FoodIndex, LibrarySearch
from dixit_answer.pipeline import Engine
from dixit_answer.review_queue import ApplyError, QueueError, ReviewQueue, _bump


def line(word="kale", kind="ingredient", turn=1, cid="c1", suggested="", **extra):
    return json.dumps({"kind": "unknown_item", "at": "2026-10-16T10:00:00", "conversation_id": cid, "turn": turn, "word": word,
                       "item_kind": kind, "suggested_food_id": suggested, **extra}, ensure_ascii=False) + "\n"


@pytest.fixture
def env(tmp_path, engine):
    for name in ("food_master.json", "allergen_map.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")

    class E:
        queue = ReviewQueue(tmp_path / "q.sqlite3", audit)
        audit_path, dir, eng = audit, tmp_path, engine
        index = FoodIndex(engine.data.food_master, engine.words["spelling_map"])
        fm, al = tmp_path / "food_master.json", tmp_path / "allergen_map.json"
        cats, alg = {"vegetable", "dairy", "pantry", "nut"}, {"milk", "gluten", "peanut"}

        def add(self, text):
            with open(audit, "ab") as h:
                h.write(text.encode("utf-8"))

        def record(self, query="kale raw"):
            return [h for h in LibrarySearch(engine.library).search(query, 10) if not engine.library.is_indian(h["record_id"])][0]["record_id"]

        def decide(self, word, kind="ingredient", action="alias", by="Dr Rao", **f):
            return self.queue.decide(word, kind, action, by, self.index, engine.library, self.alg, self.cats, **f)

        def new_food(self, word, **over):
            f = dict(name=word, category="vegetable", allergens=[], allergens_confirmed=True, record_id=self.record())
            f.update(over)
            return self.decide(word, action="new_food", **f)

        def foods(self):
            return json.loads(self.fm.read_text(encoding="utf-8"))["foods"]
    return E()


# ---------------------------------------------------------------- words people type
@pytest.mark.parametrize("raw, kept", [
    ("Kasuri METHI", "kasuri methi"), ("  spaced   out  ", "spaced out"), ("मेथी", None), ("🍛🍛", None), ("مرحبا", None), ("x", None),
    ("!!!", None), ("<b>bold</b>", "b bold b"), ("'; DROP TABLE items;--", "' drop table items"), ("a" * 300, "a" * 80), ("new\nline", "new line"),
    ("tab\tseparated", "tab separated"), ("ma's kitchen", "ma's kitchen"), ("null\x00byte", None), ("NaN", "nan"), ("1234", "1234"),
])
def test_any_word_is_stored_safely_or_ignored(env, raw, kept):
    env.add(line(word=raw))
    env.queue.sync()
    words = [i["word"] for i in env.queue.items()]
    if kept is None:
        assert len(words) <= 1                                 # ignored, or reduced to something harmless
    else:
        assert kept in words
    assert env.queue.counts() is not None
    with sqlite3.connect(env.queue.db_path) as con:            # the table is still there after the SQL-looking word
        assert con.execute("SELECT COUNT(*) FROM items").fetchone()


def test_the_same_word_in_different_cases_and_spacings_is_one_item(env):
    for i, w in enumerate(["Kale", "KALE", "kale ", " kale", "kale!"]):
        env.add(line(word=w, turn=i + 1))
    env.queue.sync()
    items = env.queue.items()
    assert len(items) == 1 and items[0]["count"] == 5


def test_a_word_asked_thousands_of_times_is_counted_not_duplicated(env):
    env.add("".join(line(word="kale", turn=i, cid=f"c{i % 50}") for i in range(3000)))
    started = time.perf_counter()
    env.queue.sync()
    assert env.queue.items()[0]["count"] == 3000 and time.perf_counter() - started < 15


# ---------------------------------------------------------------- reading the log
def test_many_threads_syncing_at_once_never_double_count_or_crash(env):
    env.add("".join(line(word=f"word{i % 40}", turn=i, cid="c") for i in range(800)))
    errors = []

    def work():
        try:
            for _ in range(5):
                env.queue.sync()
        except Exception as exc:                              # noqa: BLE001
            errors.append(repr(exc))
    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert sum(i["count"] for i in env.queue.items()) == 800


def test_sync_while_the_log_is_being_appended_to(env):
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set() and n < 400:
            env.add(line(word=f"live{n % 7}", turn=n))
            n += 1
    t = threading.Thread(target=writer)
    t.start()
    for _ in range(20):
        env.queue.sync()
    stop.set()
    t.join()
    env.queue.sync()
    written = len(env.audit_path.read_text(encoding="utf-8").splitlines())
    assert written > 0 and sum(i["count"] for i in env.queue.items()) == written       # no line lost, none counted twice


def test_a_truncated_replaced_or_deleted_log(env):
    env.add(line(word="first") + line(word="second", turn=2))
    env.queue.sync()
    env.audit_path.write_text(line(word="third", turn=9), encoding="utf-8")                 # smaller: truncated and rewritten
    env.queue.sync()
    assert {i["word"] for i in env.queue.items()} == {"first", "second", "third"}
    env.audit_path.write_text(line(word="fourth", turn=1, cid="zz") * 1 + line(word="fifth", turn=2, cid="zz") * 40, encoding="utf-8")   # replaced, larger
    env.queue.sync()
    assert {"fourth", "fifth"} <= {i["word"] for i in env.queue.items()}
    env.audit_path.unlink()
    assert env.queue.sync() == 0                                                              # a missing log is fine
    env.add(line(word="sixth", turn=3))
    env.queue.sync()
    assert "sixth" in {i["word"] for i in env.queue.items()}


@pytest.mark.parametrize("record", [
    {"kind": "unknown_item"}, {"kind": "unknown_item", "word": None}, {"kind": "unknown_item", "word": ["x"]},
    {"kind": "unknown_item", "word": "ok", "turn": "abc"}, {"kind": "unknown_item", "word": "ok", "item_kind": "banana"},
    {"kind": "turn", "recipe_trace": "R1: unknown ['x']", "plan": "oops"}, {"kind": "turn", "recipe_trace": [None, 5, "R1: unknown [broken"], "plan": []},
    {"kind": "turn", "recipe_trace": ["R1: unknown [1, 2, ('a','b')], left"], "plan": {"unknown_dish": 5}}, [], "text", 7,
])
def test_odd_log_lines_never_crash_the_sync(env, record):
    env.add(json.dumps(record) + "\n" + line(word="fine", turn=5))
    env.queue.sync()
    assert "fine" in {i["word"] for i in env.queue.items()}


# ---------------------------------------------------------------- decisions with the wrong types
@pytest.mark.parametrize("bad", [None, 5, ["FOOD-ALOO"], {"a": 1}, True, ""])
def test_alias_with_a_bad_food_id_is_a_plain_message(env, bad):
    env.add(line(word="kale"))
    env.queue.sync()
    with pytest.raises(QueueError):
        env.decide("kale", food_id=bad)


@pytest.mark.parametrize("field, bad", [
    ("name", ["x"]), ("name", 5), ("name", "x"), ("category", ["vegetable"]), ("category", None), ("category", {"a": 1}),
    ("allergens", "milk"), ("allergens", [["milk"]]), ("allergens", [5]), ("allergens", [None]), ("allergens", {"milk": 1}),
    ("allergens_confirmed", "yes"), ("allergens_confirmed", 1), ("record_id", ["USDA-1"]), ("record_id", 5), ("record_id", None),
    ("aliases", "kale leaves"), ("aliases", [5]), ("aliases", ["a"] * 11), ("aliases", [["x"]]),
])
def test_new_food_with_a_bad_field_is_a_plain_message_never_a_crash(env, field, bad):
    env.add(line(word="kale"))
    env.queue.sync()
    with pytest.raises(QueueError):
        env.new_food("kale", **{field: bad})


@pytest.mark.parametrize("by", [None, 5, "", " ", "a", ["Dr"], {"n": 1}])
def test_who_is_deciding_must_be_a_real_name(env, by):
    env.add(line(word="kale"))
    env.queue.sync()
    with pytest.raises(QueueError):
        env.queue.decide("kale", "ingredient", "reject", by, env.index, env.eng.library, env.alg, env.cats, reason="not a food")


def test_names_with_markup_or_huge_length_are_cleaned_and_cut(env):
    env.add(line(word="kale"))
    env.queue.sync()
    env.new_food("kale", name="<script>alert(1)</script> " + "very long " * 30, aliases=["<b>k</b>"])
    item = env.queue.items()[0]
    assert "<" not in json.dumps(item["decision"]) and len(item["decision"]["name"]) <= 60


def test_allergens_are_deduplicated_and_sorted(env):
    env.add(line(word="kale"))
    env.queue.sync()
    env.new_food("kale", allergens=["peanut", "milk", "milk"])
    assert env.queue.items()[0]["decision"]["allergens"] == ["milk", "peanut"]


def test_deciding_twice_replaces_the_decision_and_reopen_clears_it(env):
    env.add(line(word="kale"))
    env.queue.sync()
    env.decide("kale", action="reject", reason="not a food")
    env.decide("kale", food_id="FOOD-PALAK")
    assert env.queue.items()[0]["decision"] == {"action": "alias", "food_id": "FOOD-PALAK"}
    env.queue.reopen("kale", "ingredient")
    assert env.queue.items()[0]["status"] == "open" and env.queue.items()[0]["decision"] == {}
    with pytest.raises(QueueError):
        env.queue.reopen("never seen", "ingredient")


# ---------------------------------------------------------------- applying
def test_unreadable_or_odd_food_files_are_a_clear_error_and_change_nothing(env):
    env.add(line(word="kale"))
    env.queue.sync()
    env.decide("kale", food_id="FOOD-PALAK")
    allergens_before = env.al.read_text(encoding="utf-8")
    for content in ("{not json", "[]", '{"foods": "x"}', ""):
        env.fm.write_text(content, encoding="utf-8")
        with pytest.raises(ApplyError, match="cannot be read"):
            env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
        assert env.al.read_text(encoding="utf-8") == allergens_before
    env.fm.unlink()
    with pytest.raises(ApplyError):
        env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    assert env.queue.items()[0]["status"] == "decided"


@pytest.mark.skipif(os.name != "nt", reason="read-only behaviour differs")
def test_a_file_that_cannot_be_written_puts_everything_back(env):
    env.add(line(word="kale"))
    env.queue.sync()
    env.decide("kale", food_id="FOOD-PALAK")
    before = (env.fm.read_text(encoding="utf-8"), env.al.read_text(encoding="utf-8"))
    os.chmod(env.al, stat.S_IREAD)                           # the second file cannot be replaced
    try:
        with pytest.raises(ApplyError, match="could not be written"):
            env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    finally:
        os.chmod(env.al, stat.S_IWRITE | stat.S_IREAD)
    assert (env.fm.read_text(encoding="utf-8"), env.al.read_text(encoding="utf-8")) == before
    assert env.queue.items()[0]["status"] == "decided" and not list(env.dir.glob("*.tmp"))


def test_two_decisions_for_the_same_new_name_second_is_skipped_first_still_applies(env):
    env.add(line(word="kale") + line(word="kale leaves", turn=2))
    env.queue.sync()
    env.new_food("kale", name="kale")
    env.new_food("kale leaves", name="kale")                  # the same food name again
    result = env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 1 and len(result["skipped"]) == 1 and "already used" in result["skipped"][0]["why"]
    assert sum(1 for f in env.foods() if f["name"] == "kale") == 1
    skipped = next(i for i in env.queue.items() if i["status"] == "decided")
    assert "already used" in skipped["decision"]["not_applied_because"]


def test_an_alias_that_another_food_already_owns_is_skipped_and_one_bad_item_never_blocks_the_rest(env):
    env.add(line(word="okra fry") + line(word="lady finger fry", turn=2))
    env.queue.sync()
    env.decide("okra fry", food_id="FOOD-BHINDI")
    env.decide("lady finger fry", food_id="FOOD-BHINDI")
    fm = json.loads(env.fm.read_text(encoding="utf-8"))
    next(f for f in fm["foods"] if f["id"] == "FOOD-ALOO")["aliases"].append("okra fry")
    env.fm.write_text(json.dumps(fm), encoding="utf-8")
    result = env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 1 and [s["word"] for s in result["skipped"]] == ["okra fry"]
    assert "lady finger fry" in next(f for f in env.foods() if f["id"] == "FOOD-BHINDI")["aliases"]


def test_an_alias_already_on_the_same_food_is_not_added_twice(env):
    env.add(line(word="okra"))
    env.queue.sync()
    env.decide("okra", food_id="FOOD-BHINDI")
    before = next(f for f in env.foods() if f["id"] == "FOOD-BHINDI")["aliases"].count("okra")
    env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    assert next(f for f in env.foods() if f["id"] == "FOOD-BHINDI")["aliases"].count("okra") == max(before, 1)


def test_an_allergen_removed_from_the_map_after_the_decision_skips_the_item(env):
    env.add(line(word="khoa"))
    env.queue.sync()
    env.new_food("khoa", allergens=["milk"])
    al = json.loads(env.al.read_text(encoding="utf-8"))
    del al["allergens"]["milk"]
    al["major"] = [m for m in al["major"] if m != "milk"]
    env.al.write_text(json.dumps(al), encoding="utf-8")
    result = env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 0 and "no longer in the allergen map" in result["skipped"][0]["why"]


def test_a_food_that_was_deleted_after_an_alias_decision_skips_the_item(env):
    env.add(line(word="okra fry"))
    env.queue.sync()
    env.decide("okra fry", food_id="FOOD-BHINDI")
    fm = json.loads(env.fm.read_text(encoding="utf-8"))
    fm["foods"] = [f for f in fm["foods"] if f["id"] != "FOOD-BHINDI"]
    env.fm.write_text(json.dumps(fm), encoding="utf-8")
    result = env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 0 and "no longer exists" in result["skipped"][0]["why"]


def test_many_items_apply_together_and_a_second_apply_does_nothing(env):
    words = [f"zzfood{i}" for i in range(30)]
    env.add("".join(line(word=w, turn=i) for i, w in enumerate(words)))
    env.queue.sync()
    for w in words:
        env.new_food(w, allergens=["milk"] if w.endswith("7") else [])
    started = time.perf_counter()
    result = env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")
    assert result["applied"] == 30 and not result["skipped"] and time.perf_counter() - started < 60
    ids = [f["id"] for f in env.foods()]
    assert len(ids) == len(set(ids))
    assert env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")["applied"] == 0


def test_two_applies_at_once_apply_each_decision_exactly_once(env):
    words = [f"zzfood{i}" for i in range(6)]
    env.add("".join(line(word=w, turn=i) for i, w in enumerate(words)))
    env.queue.sync()
    for w in words:
        env.new_food(w)
    results, errors = [], []

    def go():
        try:
            results.append(env.queue.apply(env.eng, env.fm, env.al, "Dr Rao")["applied"])
        except Exception as exc:                              # noqa: BLE001
            errors.append(repr(exc))
    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and sorted(results) == [0, 0, 0, 6]
    assert sum(1 for f in env.foods() if f["name"].startswith("zzfood")) == 6


@pytest.mark.parametrize("before, after", [("FM-0.2.2", "FM-0.2.3"), ("FM-0.2.9", "FM-0.2.10"), ("v7", "v8"), ("draft", "draft.1"), ("", ".1"), (None, "None.1")])
def test_version_bumps(before, after):
    assert _bump(before) == after


def test_a_decision_reaches_a_real_chat_the_next_time_the_engine_starts(env, monkeypatch, tmp_path_factory):
    """The point of the whole page: an approved word is understood in the next chat, with no code change."""
    data_copy = tmp_path_factory.mktemp("data")
    for p in DATA_DIR.iterdir():
        if p.is_file():
            shutil.copy(p, data_copy / p.name)
    monkeypatch.setattr(loader, "DATA_DIR", data_copy)
    env.add(line(word="okra fry"))
    env.queue.sync()
    env.decide("okra fry", food_id="FOOD-BHINDI")
    env.queue.apply(env.eng, data_copy / "food_master.json", data_copy / "allergen_map.json", "Dr Rao")
    settings = env.eng.settings
    new_engine = Engine(settings, llm=env.eng.llm)
    assert new_engine.data.food_master["version"] == "FM-0.3.3"
    assert [m.food_id for m in new_engine.index.find("I have okra fry at home")] == ["FOOD-BHINDI"]
    answer = new_engine.answer("I have okra fry, what can I cook?", None, {"allergies": []})
    assert "okra fry" not in " ".join(answer.plan.get("ingredients", [])) and "isn't in our food list" not in answer.answer
