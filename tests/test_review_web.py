"""Plan item D: the dietitian review page and its API."""
import json
import shutil
import sqlite3

from fastapi.testclient import TestClient

from dixit_answer.config import DATA_DIR
from dixit_answer.web import create_app


def unknown(word, kind="ingredient", turn=1, suggested=""):
    return json.dumps({"kind": "unknown_item", "at": "2026-10-16T10:00:00", "conversation_id": "web-test", "turn": turn,
                       "word": word, "item_kind": kind, "suggested_food_id": suggested}) + "\n"


def make_client(engine, tmp_path):
    for name in ("food_master.json", "allergen_map.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    app = create_app(engine, data_dir=tmp_path)
    return app, TestClient(app)


def test_page_and_empty_state(engine, tmp_path):
    _, client = make_client(engine, tmp_path)
    page = client.get("/review")
    assert page.status_code == 200 and "Unknown foods and dishes" in page.text
    data = client.get("/api/review/items").json()
    assert "food_master_version" in data and "milk" in data["allergens"] and "vegetable" in data["categories"]


def test_decide_and_apply_through_the_api(engine, tmp_path):
    app, client = make_client(engine, tmp_path)
    with open(engine.settings.audit_log, "a", encoding="utf-8") as h:
        h.write(unknown("zzlady finger stir fry", suggested="FOOD-BHINDI"))
        h.write(unknown("zzkothimbir vadi", kind="dish"))
    items = client.get("/api/review/items").json()["items"]
    mine = {(i["word"], i["kind"]): i for i in items}
    assert mine[("zzlady finger stir fry", "ingredient")]["suggested_food_id"] == "FOOD-BHINDI"
    # nothing is decided yet, so there is nothing to apply
    assert client.post("/api/review/apply", json={"by": "Dr Rao"}).json()["applied"] == 0
    # problems come back as plain messages
    bad = client.post("/api/review/decide", json={"word": "zzlady finger stir fry", "kind": "ingredient", "action": "alias",
                                                  "by": "Dr Rao", "food_id": "FOOD-NOPE"})
    assert bad.status_code == 400 and "Choose which food" in bad.json()["detail"]
    assert client.post("/api/review/decide", json={"word": "zzlady finger stir fry", "kind": "ingredient", "action": "alias",
                                                   "by": "", "food_id": "FOOD-BHINDI"}).status_code == 400
    ok = client.post("/api/review/decide", json={"word": "zzlady finger stir fry", "kind": "ingredient", "action": "alias",
                                                 "by": "Dr Rao", "food_id": "FOOD-BHINDI"})
    assert ok.status_code == 200 and ok.json()["status"] == "decided"
    plan = client.post("/api/review/decide", json={"word": "zzkothimbir vadi", "kind": "dish", "action": "plan", "by": "Dr Rao",
                                                   "note": "snack"})
    assert plan.json()["status"] == "planned"
    reopened = client.post("/api/review/reopen", json={"word": "zzkothimbir vadi", "kind": "dish"})
    assert reopened.json() == {"ok": True}
    assert client.post("/api/review/apply", json={"by": ""}).status_code == 400
    result = client.post("/api/review/apply", json={"by": "Dr Rao"}).json()
    assert result["applied"] == 1 and result["version"] == "FM-0.2.3"
    fm = json.loads((tmp_path / "food_master.json").read_text(encoding="utf-8"))
    assert "zzlady finger stir fry" in next(f for f in fm["foods"] if f["id"] == "FOOD-BHINDI")["aliases"]
    after = {(i["word"], i["kind"]): i for i in client.get("/api/review/items").json()["items"]}
    assert after[("zzlady finger stir fry", "ingredient")]["status"] == "applied"
    assert after[("zzkothimbir vadi", "dish")]["status"] == "open"


def test_search_returns_usda_records_only(engine, tmp_path):
    _, client = make_client(engine, tmp_path)
    results = client.get("/api/review/search", params={"q": "kale raw"}).json()["results"]
    assert results and all(not engine.library.is_indian(r["record_id"]) for r in results)
    assert "energy_kcal" in results[0]["per_100g"]
    assert client.get("/api/review/search", params={"q": "k"}).status_code == 422


def test_a_bad_item_is_skipped_with_a_reason_and_changes_nothing(engine, tmp_path):
    app, client = make_client(engine, tmp_path)
    with open(engine.settings.audit_log, "a", encoding="utf-8") as h:
        h.write(unknown("zzkale"))
    client.get("/api/review/items")
    record = client.get("/api/review/search", params={"q": "kale raw"}).json()["results"][0]["record_id"]
    ok = client.post("/api/review/decide", json={"word": "zzkale", "kind": "ingredient", "action": "new_food", "by": "Dr Rao",
                                                 "name": "zzkale", "category": "vegetable", "allergens": [], "allergens_confirmed": True,
                                                 "record_id": record})
    assert ok.status_code == 200
    with sqlite3.connect(app.state.queue.db_path) as con:
        con.execute("UPDATE items SET decision=? WHERE word='zzkale'", (json.dumps(
            {"action": "new_food", "name": "zzkale", "category": "vegetable", "allergens": [], "record_id": "NOT-A-RECORD", "aliases": []}),))
    before = (tmp_path / "food_master.json").read_text(encoding="utf-8")
    res = client.post("/api/review/apply", json={"by": "Dr Rao"})
    assert res.status_code == 200 and res.json()["applied"] == 0 and "NOT-A-RECORD" in res.json()["skipped"][0]["why"]
    assert (tmp_path / "food_master.json").read_text(encoding="utf-8") == before


def test_words_from_people_are_never_trusted_as_markup(engine, tmp_path):
    """The page builds every word with textContent; the API stores it as plain text and the page script has no innerHTML."""
    _, client = make_client(engine, tmp_path)
    page = client.get("/review").text
    assert "innerHTML" not in page and "document.write" not in page and "eval(" not in page
    with open(engine.settings.audit_log, "a", encoding="utf-8") as h:
        h.write(unknown("<img src=x onerror=alert(1)>"))
    words = [i["word"] for i in client.get("/api/review/items").json()["items"]]
    assert all("<" not in w for w in words)          # the cleaner strips markup characters before the word is stored
