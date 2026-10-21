"""DIXIT_HOST: the server can listen on the network for a teammate, and the review pages (they change files) stay on this machine."""
import copy
import shutil

import pytest
from fastapi.testclient import TestClient

import dixit_answer.web as web
from dixit_answer.config import DATA_DIR, LOOPBACK_HOST, is_loopback, load_settings, parse_host
from dixit_answer.web import create_app, network_urls

REVIEW_ROUTES = [("get", "/review"), ("get", "/api/review/items"), ("get", "/api/review/search?q=dal"),
                 ("post", "/api/review/decide"), ("post", "/api/review/reopen"), ("post", "/api/review/apply")]


# ---------------------------------------------------------------- the setting
def test_the_default_is_this_machine_only(monkeypatch):
    monkeypatch.delenv("DIXIT_HOST", raising=False)
    assert load_settings().host == LOOPBACK_HOST == "127.0.0.1"


@pytest.mark.parametrize("value, expected", [("", "127.0.0.1"), ("   ", "127.0.0.1"), ("0.0.0.0", "0.0.0.0"), ("::", "::"), ("[::1]", "::1"),
                                             ("192.168.1.20", "192.168.1.20"), (" 192.168.1.20 ", "192.168.1.20"), ("localhost", "localhost"),
                                             ("My-PC.local", "my-pc.local"), ("127.0.0.2", "127.0.0.2")])
def test_valid_hosts(value, expected):
    assert parse_host(value) == expected


@pytest.mark.parametrize("value", ["a b", "http://x", "x:8100", "bad_name", "1.2.3.4.5", "999.1.1.1", "300", "-a", "a-", "a..b", ".a", "a.", "x" * 300,
                                   "host;rm -rf", "$(id)", "0.0.0.0/0", "ho\nst", "पोहा"])
def test_invalid_hosts_are_refused_with_a_plain_message(value):
    with pytest.raises(ValueError, match="DIXIT_HOST must be"):
        parse_host(value)


def test_the_environment_variable_and_the_env_file_are_read(monkeypatch):
    monkeypatch.setenv("DIXIT_HOST", "0.0.0.0")
    assert load_settings().host == "0.0.0.0"
    monkeypatch.setenv("DIXIT_HOST", "not a host")
    with pytest.raises(ValueError):
        load_settings()
    assert "DIXIT_HOST" in (DATA_DIR.parent.parent / ".env.example").read_text(encoding="utf-8")


def test_an_override_wins_like_every_other_setting(monkeypatch):
    monkeypatch.setenv("DIXIT_HOST", "0.0.0.0")
    assert load_settings(host="127.0.0.1").host == "127.0.0.1"


@pytest.mark.parametrize("host, expected", [("127.0.0.1", True), ("127.0.0.2", True), ("::1", True), ("[::1]", True), ("localhost", True), ("LOCALHOST", True),
                                            ("0.0.0.0", False), ("::", False), ("192.168.1.2", False), ("10.0.0.5", False), ("example.com", False), ("", False),
                                            ("::ffff:192.168.1.2", False), ("not-an-ip", False), (None, False)])
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


# ---------------------------------------------------------------- the review pages
def _client(engine, monkeypatch, host, client_address, tmp_path):
    settings = copy.copy(engine.settings)
    monkeypatch.setattr(engine, "settings", settings)
    monkeypatch.setattr(settings, "host", host)
    for name in ("food_master.json", "allergen_map.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    return TestClient(create_app(engine, data_dir=tmp_path), client=client_address)


def _call(client, method, url):
    return client.get(url) if method == "get" else client.post(url, json={})


@pytest.mark.parametrize("method, url", REVIEW_ROUTES)
def test_on_the_network_another_machine_cannot_reach_the_review_pages(engine, monkeypatch, tmp_path, method, url):
    client = _client(engine, monkeypatch, "0.0.0.0", ("192.168.1.50", 51000), tmp_path)
    response = _call(client, method, url)
    assert response.status_code == 403 and "machine that runs this server" in response.text


@pytest.mark.parametrize("method, url", REVIEW_ROUTES)
@pytest.mark.parametrize("address", [("127.0.0.1", 5000), ("::1", 5000)])
def test_on_the_network_this_machine_still_reaches_them(engine, monkeypatch, tmp_path, method, url, address):
    client = _client(engine, monkeypatch, "0.0.0.0", address, tmp_path)
    assert _call(client, method, url).status_code != 403


@pytest.mark.parametrize("address", [("192.168.1.50", 1), ("10.1.2.3", 1), ("::ffff:192.168.1.50", 1), ("localhost.evil.com", 1), ("", 1)])
def test_lookalike_and_empty_client_addresses_are_not_this_machine(engine, monkeypatch, tmp_path, address):
    client = _client(engine, monkeypatch, "0.0.0.0", address, tmp_path)
    assert client.get("/api/review/items").status_code == 403


def test_the_chat_stays_open_to_another_machine(engine, monkeypatch, tmp_path):
    client = _client(engine, monkeypatch, "0.0.0.0", ("192.168.1.50", 51000), tmp_path)
    assert client.get("/").status_code == 200 and client.get("/api/info").status_code == 200
    reply = client.post("/api/chat", json={"message": "how many calories in dal"})
    assert reply.status_code == 200 and "calories" in reply.json()["answer"]
    assert client.post("/api/feedback", json={"conversation_id": reply.json()["conversation_id"], "turn": 1, "kind": "helpful"}).status_code == 200


def test_with_the_default_host_nothing_changes(engine, monkeypatch, tmp_path):
    client = _client(engine, monkeypatch, "127.0.0.1", ("testclient", 50000), tmp_path)
    assert client.get("/api/review/items").status_code == 200


def test_production_still_refuses_the_review_pages_everywhere(engine, monkeypatch, tmp_path):
    client = _client(engine, monkeypatch, "0.0.0.0", ("127.0.0.1", 5000), tmp_path)
    monkeypatch.setattr(engine.settings, "environment", "production")
    assert client.get("/api/review/items").status_code == 403


# ---------------------------------------------------------------- start-up
def test_start_up_passes_the_host_to_the_server_and_warns_on_the_network(monkeypatch, capsys):
    started = {}
    monkeypatch.setenv("DIXIT_HOST", "0.0.0.0")
    monkeypatch.setenv("DIXIT_PORT", "8123")
    monkeypatch.setattr(web, "create_app", lambda: "app")
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: started.update(app=app, host=host, port=port))
    web.main()
    assert started == {"app": "app", "host": "0.0.0.0", "port": 8123}
    out = capsys.readouterr().out
    assert "open to anyone" in out and "review pages stay on this machine" in out and ":8123" in out


def test_start_up_on_this_machine_prints_no_warning(monkeypatch, capsys):
    started = {}
    monkeypatch.delenv("DIXIT_HOST", raising=False)
    monkeypatch.setattr(web, "create_app", lambda: "app")
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: started.update(host=host))
    web.main()
    assert started["host"] == "127.0.0.1" and capsys.readouterr().out == ""


def test_network_urls():
    assert network_urls("192.168.1.5", 8100) == ["http://192.168.1.5:8100"]
    urls = network_urls("0.0.0.0", 8100)
    assert urls and all(u.startswith("http://") and u.endswith(":8100") and "127.0.0.1" not in u for u in urls)
