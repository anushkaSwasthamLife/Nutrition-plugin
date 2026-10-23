"""Past conversations and remembered facts: recorded per person, shown on the chat page, deletable (development only)."""
import pytest
from fastapi.testclient import TestClient

from dixit_answer.config import load_settings
from dixit_answer.llm import MockLLM
from dixit_answer.pipeline import Engine
from dixit_answer.web import create_app

ME, OTHER = "person-aaaaaa1", "person-bbbbbb2"


@pytest.fixture
def client(tmp_path):
    engine = Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path, dish_lookup=False), llm=MockLLM())
    return TestClient(create_app(engine))


def say(client, message, cid=None, user=ME):
    return client.post("/api/chat", json={"message": message, "conversation_id": cid, "user_id": user}).json()


def test_a_chat_shows_up_in_past_conversations(client):
    first = say(client, "what sabji can i eat with rice?")
    say(client, "bhindi", first["conversation_id"])
    data = client.get(f"/api/memory/{ME}").json()
    assert len(data["conversations"]) == 1
    conv = data["conversations"][0]
    assert conv["conversation_id"] == first["conversation_id"] and conv["turns"] == 2 and conv["title"].startswith("what sabji")


def test_opening_a_past_conversation_gives_every_question_and_answer(client):
    first = say(client, "what sabji can i eat with rice?")
    say(client, "bhindi", first["conversation_id"])
    turns = client.get(f"/api/history/{ME}/{first['conversation_id']}").json()["turns"]
    assert [t["question"] for t in turns] == ["what sabji can i eat with rice?", "bhindi"] and all(t["answer"] for t in turns)
    assert client.get(f"/api/history/{ME}/{first['conversation_id']}/export").text.startswith("# Conversation")


def test_nobody_can_open_another_persons_conversation(client):
    first = say(client, "what sabji can i eat with rice?")
    cid = first["conversation_id"]
    assert client.get(f"/api/history/{OTHER}/{cid}").status_code == 404
    assert client.delete(f"/api/history/{OTHER}/{cid}").status_code == 404
    assert client.get(f"/api/history/{ME}/{cid}").status_code == 200
    assert client.get(f"/api/memory/{OTHER}").json()["conversations"] == []


def test_a_chat_without_a_person_is_not_kept(client):
    client.post("/api/chat", json={"message": "what sabji can i eat with rice?"})
    assert client.get(f"/api/memory/{ME}").json()["conversations"] == []


def test_a_bad_person_id_is_refused(client):
    assert client.get("/api/memory/x").status_code == 404
    assert client.get("/api/memory/..%2F..%2Fetc").status_code in {404, 422}


def test_a_new_chat_starts_from_what_was_said_before(client):
    first = say(client, "I am allergic to milk")
    facts = client.get(f"/api/memory/{ME}").json()["facts"]
    assert any("milk" in f["text"].lower() for f in facts)
    second = say(client, "what sabji can i eat with rice?")                          # no conversation id: a new chat
    assert second["conversation_id"] != first["conversation_id"]
    assert any(item["key"] == "allergies" for item in second["remembered"])
    third = say(client, "bhindi", second["conversation_id"])
    assert third["remembered"] == []                                                  # said once, at the start of the chat


def test_someone_elses_facts_are_never_used(client):
    say(client, "I am allergic to milk")
    other = say(client, "what sabji can i eat with rice?", user=OTHER)
    assert other["remembered"] == []


def test_a_fact_can_be_forgotten(client):
    say(client, "I am allergic to milk")
    assert client.delete(f"/api/memory/{ME}/fact/allergies").json() == {"ok": True}
    assert not [f for f in client.get(f"/api/memory/{ME}").json()["facts"] if f["key"] == "allergies"]
    assert say(client, "what sabji can i eat with rice?")["remembered"] == []


def test_a_conversation_can_be_deleted(client):
    first = say(client, "what sabji can i eat with rice?")
    assert client.delete(f"/api/history/{ME}/{first['conversation_id']}").json() == {"ok": True}
    assert client.get(f"/api/memory/{ME}").json()["conversations"] == []
    assert client.get(f"/api/history/{ME}/{first['conversation_id']}").status_code == 404


def test_forget_everything_removes_facts_and_chats(client):
    say(client, "I am allergic to milk")
    say(client, "what sabji can i eat with rice?")
    client.delete(f"/api/memory/{ME}")
    data = client.get(f"/api/memory/{ME}").json()
    assert data["conversations"] == [] and data["facts"] == []


def test_a_chat_whose_state_is_lost_never_overwrites_its_history(client):
    first = say(client, "what sabji can i eat with rice?")
    cid = first["conversation_id"]
    client.post(f"/api/reset/{cid}")                                                  # the state row goes, the history stays
    again = say(client, "bhindi", cid)
    assert again["turn"] == 2
    assert [t["question"] for t in client.get(f"/api/history/{ME}/{cid}").json()["turns"]] == ["what sabji can i eat with rice?", "bhindi"]


def test_feedback_is_kept_with_the_turn(client):
    first = say(client, "what sabji can i eat with rice?")
    client.post("/api/feedback", json={"conversation_id": first["conversation_id"], "turn": 1, "kind": "something_wrong"})
    assert client.get(f"/api/memory/{ME}").json()["conversations"][0]["needs_look"] == 1


def test_it_is_off_when_the_setting_says_so(tmp_path):
    engine = Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=tmp_path, dish_lookup=False, memory=False), llm=MockLLM())
    client = TestClient(create_app(engine))
    say(client, "what sabji can i eat with rice?")
    assert client.get(f"/api/memory/{ME}").status_code == 404


def test_remembered_foods_show_as_names_not_ids(client):
    first = say(client, "what sabji can i eat with rice?")
    say(client, "bhindi", first["conversation_id"])
    texts = [f["text"] for f in client.get(f"/api/memory/{ME}").json()["facts"]]
    assert texts and not any("FOOD-" in t for t in texts)


def test_the_page_has_the_past_conversations_panel(client):
    html = client.get("/").text
    assert "Past conversations" in html and "What I remember" in html and 'id="convs"' in html
