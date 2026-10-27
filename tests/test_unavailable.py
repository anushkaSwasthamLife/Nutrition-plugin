"""When the system cannot do its job safely: the audit log cannot be written, or the rules and limits cannot be loaded."""
import copy
import json
import re

import pytest
from fastapi.testclient import TestClient

import dixit_answer.web as web
from dixit_answer import unavailable
from dixit_answer.checks import operational_claims
from dixit_answer.llm import MockLLM
from dixit_answer.loader import LoadError
from dixit_answer.pipeline import AuditLog, Engine
from dixit_answer.unavailable import AuditUnavailable
from dixit_answer.web import create_app


def broken(path):
    """Make writing the log fail: a directory where the file should be."""
    path.mkdir()


# ---------------------------------------------------------------- the audit log itself
def test_lines_wait_in_order_and_are_written_when_the_log_works_again(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append({"kind": "turn", "type": "x", "n": 1})
    broken_path = tmp_path / "audit2.jsonl"
    log2 = AuditLog(broken_path)
    broken(broken_path)
    for n in (1, 2, 3):
        log2.append({"kind": "turn", "type": "x", "n": n})
    assert log2.queued == 3 and log2.failures == 3
    broken_path.rmdir()
    log2.append({"kind": "turn", "type": "x", "n": 4})
    rows = [json.loads(line)["n"] for line in broken_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [1, 2, 3, 4] and log2.queued == 0


def test_a_full_waiting_list_withholds_the_next_answer(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    broken(path)
    log.max_queued = 2
    log.append({"kind": "turn", "type": "food_allowed"})
    log.append({"kind": "turn", "type": "food_allowed"})
    with pytest.raises(AuditUnavailable):
        log.append({"kind": "turn", "type": "food_allowed"})
    assert log.queued == 2                       # the refused line is not kept: its answer was never sent


@pytest.mark.parametrize("kind", ["emergency", "harmful_intent"])
def test_an_emergency_never_waits_for_the_log(tmp_path, kind):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    broken(path)
    log.max_queued = 0
    log.append({"kind": "turn", "type": kind})                 # does not raise
    assert log.queued == 1


def test_a_working_log_is_unchanged(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.append({"kind": "turn", "type": "x"})
    assert log.queued == 0 and log.failures == 0 and len((tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()) == 1


# ---------------------------------------------------------------- the chat API
@pytest.fixture
def fresh_engine(settings, tmp_path):
    cfg = copy.copy(settings)
    cfg.var_dir = tmp_path
    return Engine(cfg, llm=MockLLM())


def test_answers_go_out_while_lines_wait_then_are_withheld_but_an_emergency_still_goes_out(fresh_engine):
    client = TestClient(create_app(fresh_engine))
    log = fresh_engine.audit
    broken(log.path)
    log.max_queued = 2
    first = client.post("/api/chat", json={"message": "what sabji can i eat with rice?"}).json()
    assert first["type"] != "unavailable" and log.queued >= 1
    withheld = None
    for _ in range(6):
        out = client.post("/api/chat", json={"message": "what sabji can i eat with rice?"}).json()
        if out["type"] == "unavailable":
            withheld = out
            break
    assert withheld and withheld["unavailable"] == "audit" and withheld["answer"] == unavailable.AUDIT
    emergency = client.post("/api/chat", json={"message": "my throat is swelling after eating peanuts"}).json()
    assert emergency["type"] == "emergency" and "112" in emergency["answer"]
    info = client.get("/api/info").json()
    assert info["audit"]["write_failures"] >= 1 and info["audit"]["queued"] >= 2
    log.path.rmdir()
    ok = client.post("/api/chat", json={"message": "what sabji can i eat with rice?"}).json()
    assert ok["type"] != "unavailable" and log.queued == 0
    assert any(json.loads(line)["kind"] == "turn" for line in log.path.read_text(encoding="utf-8").splitlines())


def test_limits_that_cannot_be_loaded_give_the_fixed_reply_and_the_next_message_tries_again(monkeypatch, engine):
    calls = {"n": 0}

    def failing():
        calls["n"] += 1
        raise LoadError("condition_limits_test.json: cannot be read (OSError)")

    monkeypatch.setattr(web, "Engine", failing)
    client = TestClient(create_app())
    for _ in range(2):
        out = client.post("/api/chat", json={"message": "can i eat paneer?", "conversation_id": "c1"}).json()
        assert out["type"] == "unavailable" and out["unavailable"] == "limits" and out["answer"] == unavailable.LIMITS
        assert out["conversation_id"] == "c1" and out["records"] == [] and out["fallback"] is True
    assert calls["n"] == 2                       # nothing was cached: each message tried again
    monkeypatch.setattr(web, "Engine", lambda: engine)
    recovered = client.post("/api/chat", json={"message": "what sabji can i eat with rice?"}).json()
    assert recovered["type"] != "unavailable"


def test_the_fixed_replies_give_no_food_no_question_and_no_false_claim():
    for text in (unavailable.LIMITS, unavailable.AUDIT):
        assert "112" in text and "14416" in text
        assert not operational_claims(text) and "?" not in text
        assert not re.search(r"\d+ ?(g|kcal|gram)", text)


def test_a_bug_in_answering_is_not_hidden_as_unavailable(monkeypatch, engine):
    app = create_app(engine)

    def boom(*args, **kwargs):
        raise RuntimeError("a real bug")

    monkeypatch.setattr(web.MainPipeline, "answer", boom)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 500
