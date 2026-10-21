import json

from fastapi.testclient import TestClient

from dixit_answer.web import create_app


def test_chat_feedback_and_audit(engine):
    client = TestClient(create_app(engine))
    assert client.get("/").status_code == 200
    first = client.post("/api/chat", json={"message": "what sabji can i eat with rice?"}).json()
    assert first["type"] == "food_choice_guidance" and first["turn"] == 1
    second = client.post("/api/chat", json={"message": "bhindi", "conversation_id": first["conversation_id"]}).json()
    assert second["conversation_id"] == first["conversation_id"] and second["turn"] == 2
    assert client.post("/api/feedback", json={"conversation_id": first["conversation_id"], "turn": 1,
                                              "kind": "helpful"}).json() == {"ok": True}
    assert client.post("/api/feedback", json={"conversation_id": "x", "turn": 1, "kind": "bogus"}).status_code == 400
    lines = [json.loads(line) for line in engine.settings.audit_log.read_text(encoding="utf-8").splitlines()]
    turns = [l for l in lines if l["kind"] == "turn" and l["conversation_id"] == first["conversation_id"]]
    assert len(turns) == 2 and turns[0]["versions"]["library"] == "1.0.5"
    assert any(l["kind"] == "feedback" for l in lines)
