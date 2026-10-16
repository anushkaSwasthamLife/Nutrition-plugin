"""Edge cases for the review page and its API (plan item D)."""
import copy
import json
import re
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dixit_answer.config import DATA_DIR
from dixit_answer.web import STATIC, create_app


def unknown(word, kind="ingredient", turn=1, cid="web-edge"):
    return json.dumps({"kind": "unknown_item", "at": "2026-10-16T10:00:00", "conversation_id": cid, "turn": turn, "word": word,
                       "item_kind": kind, "suggested_food_id": ""}) + "\n"


@pytest.fixture
def client(engine, tmp_path):
    """Its own queue, audit log and data files, so no test can see another test's leftovers."""
    for name in ("food_master.json", "allergen_map.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    settings = copy.copy(engine.settings)
    settings.var_dir = tmp_path
    stub = SimpleNamespace(settings=settings, data=engine.data, words=engine.words, library=engine.library, llm=engine.llm)
    app = create_app(stub, data_dir=tmp_path)
    c = TestClient(app, raise_server_exceptions=False)         # a crash must show up as a 500 the tests can see
    c.app_, c.dir, c.audit = app, tmp_path, settings.audit_log
    return c


def feed(client, *lines):
    with open(client.audit, "a", encoding="utf-8") as h:
        h.write("".join(lines))


# ---------------------------------------------------------------- malformed requests are 4xx, never 500
@pytest.mark.parametrize("body", [
    "not json", "[]", "null", "42", '"text"', "{}", '{"word": "x"}', '{"word": "x", "kind": "ingredient"}',
    '{"word": ["x"], "kind": "ingredient", "action": "reject"}', '{"word": "x", "kind": 5, "action": "reject"}',
    '{"word": "x", "kind": "ingredient", "action": "reject", "by": 5}', '{"word": "x", "kind": "ingredient", "action": "reject", "by": {"a": 1}}',
    '{"word": "x", "kind": "ingredient", "action": "alias", "food_id": ["FOOD-ALOO"], "by": "Dr Rao"}',
    '{"word": "x", "kind": "ingredient", "action": "new_food", "allergens": "milk", "by": "Dr Rao"}',
    '{"word": "x", "kind": "ingredient", "action": "new_food", "allergens": [[1]], "by": "Dr Rao"}',
    '{"word": "x", "kind": "ingredient", "action": "new_food", "allergens_confirmed": "yes", "by": "Dr Rao"}',
    '{"word": "' + "x" * 81 + '", "kind": "ingredient", "action": "reject", "by": "Dr Rao"}',
    '{"word": "x", "kind": "ingredient", "action": "reject", "by": "' + "n" * 81 + '"}',
    '{"word": "x", "kind": "ingredient", "action": "reject", "reason": "' + "r" * 201 + '", "by": "Dr Rao"}',
    '{"word": "x", "kind": "ingredient", "action": "new_food", "aliases": [' + ",".join(['"a"'] * 11) + '], "by": "Dr Rao"}',
])
def test_decide_with_a_malformed_body_never_crashes(client, body):
    r = client.post("/api/review/decide", content=body, headers={"Content-Type": "application/json"})
    assert 400 <= r.status_code < 500, (r.status_code, r.text[:200])


@pytest.mark.parametrize("path, body", [
    ("/api/review/reopen", "{}"), ("/api/review/reopen", '{"word": 5, "kind": "x"}'), ("/api/review/reopen", "[]"),
    ("/api/review/apply", "[]"), ("/api/review/apply", '{"by": 5}'), ("/api/review/apply", '{"by": "' + "x" * 81 + '"}'), ("/api/review/apply", "oops"),
])
def test_reopen_and_apply_with_a_malformed_body_never_crash(client, path, body):
    r = client.post(path, content=body, headers={"Content-Type": "application/json"})
    assert 400 <= r.status_code < 500 or (path.endswith("apply") and r.status_code == 200), (r.status_code, r.text[:200])


@pytest.mark.parametrize("method, path", [("get", "/api/review/decide"), ("get", "/api/review/apply"), ("post", "/api/review/items"),
                                          ("delete", "/api/review/items"), ("put", "/review"), ("get", "/api/review/nothing"), ("get", "/reviews")])
def test_wrong_verbs_and_unknown_routes_are_404_or_405(client, method, path):
    assert getattr(client, method)(path).status_code in (404, 405)


@pytest.mark.parametrize("q, ok", [("k", False), ("", False), ("x" * 61, False), ("kale raw", True), ("  kale  ", True), (".*(", True),
                                   ("'; DROP TABLE x;--", True), ("पनीर", True), ("🍛🍛", True), ("%00", True), ("a b c d e f g h", True)])
def test_search_input_is_validated_and_never_crashes(client, q, ok):
    r = client.get("/api/review/search", params={"q": q})
    assert (r.status_code == 200) == ok and r.status_code < 500
    if r.status_code == 200:
        assert len(r.json()["results"]) <= 8


# ---------------------------------------------------------------- words from people
def test_a_hostile_word_and_a_hostile_name_travel_as_plain_text(client):
    feed(client, unknown("<img src=x onerror=alert(1)>"), unknown("\"><script>alert(1)</script>", turn=2))
    items = client.get("/api/review/items").json()["items"]
    assert all("<" not in i["word"] and ">" not in i["word"] for i in items)
    word = next(i["word"] for i in items if "script" in i["word"] or "img" in i["word"])
    r = client.post("/api/review/decide", json={"word": word, "kind": "ingredient", "action": "reject", "by": "<b>Dr</b>", "reason": "<i>no</i>"})
    assert r.status_code == 200
    stored = next(i for i in client.get("/api/review/items").json()["items"] if i["word"] == word)
    assert stored["decided_by"] == "<b>Dr</b>" and stored["decision"]["reason"] == "<i>no</i>"     # kept as text; the page shows it with textContent


def test_the_page_script_is_valid_and_never_writes_html(client):
    page = client.get("/review").text
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"", "javascript:"):
        assert banned not in page, banned
    script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
    try:
        node = subprocess.run(["node", "--check", "-"], input=script, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        pytest.skip("node is not installed")
    assert node.returncode == 0, node.stderr


# ---------------------------------------------------------------- many at once, and the wrong environment
def test_many_requests_at_once_all_succeed_and_nothing_is_lost(client):
    feed(client, "".join(unknown(f"zzpar{i}", turn=i + 1, cid="par") for i in range(30)))
    client.get("/api/review/items")
    codes, errors = [], []

    def one(i):
        try:
            r = client.post("/api/review/decide", json={"word": f"zzpar{i}", "kind": "ingredient", "action": "reject", "by": "Dr Rao", "reason": "not a food"})
            codes.append(r.status_code)
            codes.append(client.get("/api/review/items").status_code)
        except Exception as exc:                              # noqa: BLE001
            errors.append(repr(exc))
    threads = [threading.Thread(target=one, args=(i,)) for i in range(30)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and set(codes) == {200}
    done = [i for i in client.get("/api/review/items").json()["items"] if i["word"].startswith("zzpar")]
    assert len(done) == 30 and all(i["status"] == "rejected" for i in done)


def test_the_review_api_is_refused_in_production(tmp_path):
    stub = SimpleNamespace(settings=SimpleNamespace(environment="production", var_dir=tmp_path, audit_log=tmp_path / "a.jsonl"))
    c = TestClient(create_app(stub, data_dir=tmp_path), raise_server_exceptions=False)
    assert c.get("/api/review/items").status_code == 403
    assert c.post("/api/review/apply", json={"by": "Dr Rao"}).status_code == 403
    assert c.post("/api/review/decide", json={"word": "x", "kind": "ingredient", "action": "reject", "by": "Dr Rao", "reason": "no"}).status_code == 403


def test_apply_on_an_unreadable_food_list_is_a_409_with_a_plain_message(client):
    feed(client, unknown("zzbroken"))
    client.get("/api/review/items")
    assert client.post("/api/review/decide", json={"word": "zzbroken", "kind": "ingredient", "action": "alias", "by": "Dr Rao",
                                                   "food_id": "FOOD-BHINDI"}).status_code == 200
    (client.dir / "food_master.json").write_text("{broken", encoding="utf-8")
    r = client.post("/api/review/apply", json={"by": "Dr Rao"})
    assert r.status_code == 409 and "cannot be read" in " ".join(r.json()["detail"]["problems"])
    assert (client.dir / "food_master.json").read_text(encoding="utf-8") == "{broken"           # untouched


def test_the_decided_but_not_applied_reason_shows_in_the_list(client):
    feed(client, unknown("zzclash"))
    client.get("/api/review/items")
    client.post("/api/review/decide", json={"word": "zzclash", "kind": "ingredient", "action": "alias", "by": "Dr Rao", "food_id": "FOOD-BHINDI"})
    fm = json.loads((client.dir / "food_master.json").read_text(encoding="utf-8"))
    next(f for f in fm["foods"] if f["id"] == "FOOD-ALOO")["aliases"].append("zzclash")
    (client.dir / "food_master.json").write_text(json.dumps(fm), encoding="utf-8")
    res = client.post("/api/review/apply", json={"by": "Dr Rao"}).json()
    assert res["applied"] == 0 and res["skipped"][0]["word"] == "zzclash"
    item = next(i for i in client.get("/api/review/items").json()["items"] if i["word"] == "zzclash")
    assert item["status"] == "decided" and "already used" in item["decision"]["not_applied_because"]
