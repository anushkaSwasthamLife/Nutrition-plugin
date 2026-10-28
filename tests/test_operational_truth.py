"""N30: an answer never claims an action or a review that did not happen. And the audit log keeps state in separate fields."""
import json

import pytest

from dixit_answer.checks import operational_claims


@pytest.mark.parametrize("text", [
    "I have notified your coach about this.",
    "We've alerted the team.",
    "Your Dixit coach has been informed.",
    "Your doctor has reviewed your message.",
    "The dietitian is reviewing your answer.",
    "A coach will call you tomorrow.",
    "We will contact you shortly.",
    "Your report has been escalated.",
    "I've booked an appointment for you.",
    "Swastham has flagged this.",
    "The team is looking at it now.",
    "If you need help, I've notified your coach.",
    "You can relax, your coach has been notified.",
    "You may rest: the team has been alerted.",
])
def test_false_action_claims_are_caught(text):
    assert operational_claims(text)


@pytest.mark.parametrize("text", [
    "Please ask your doctor before changing anything.",
    "Talk to your Dixit coach about this.",
    "Your doctor will tell you the right amount.",
    "If you feel unwell, call 112 now.",
    "I told you earlier that dal is fine.",
    "Call your doctor if the swelling returns.",
    "Your coach can help with this.",
    "Consult a doctor; this is not a diagnosis.",
    "Paneer is a good source of protein.",
    "This changes only when your Dixit expert has checked your results and cleared you.",
    "If your doctor has reviewed it, follow their advice.",
    "Once the team has seen your report they can help.",
])
def test_ordinary_advice_is_not_a_claim(text):
    assert not operational_claims(text)


def test_fixed_replies_and_templates_make_no_claims(engine):
    from dixit_answer.config import DATA_DIR
    for name in ("emergency_rules.json", "harmful_intent_rules.json", "support_content.json", "reply_translations.json"):
        raw = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
        texts = []

        def walk(node):
            if isinstance(node, str):
                texts.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(raw)
        bad = [(t[:80], operational_claims(t)) for t in texts if operational_claims(t)]
        assert not bad, f"{name}: {bad[:3]}"


def test_a_model_draft_that_claims_a_notification_is_rejected_and_the_template_is_sent(engine):
    from dixit_answer.llm import MockLLM
    from dixit_answer.pipeline import Engine

    class Claiming(MockLLM):
        name = "claiming"                 # the pipeline only asks a model that is not the mock to reword

        def reword(self, *args, **kwargs):
            text = super().reword(*args, **kwargs)
            return text + " I have notified your coach."

    probe = Engine(engine.settings, llm=Claiming())
    result = probe.answer("what is a good lunch with dal")
    assert any(c["check_id"] == "N30" and not c["passed"] for c in result.checks), "the claim was not caught"
    assert "notified" not in result.answer.lower() and result.fallback


def _last(engine):
    return [json.loads(line) for line in engine.settings.audit_log.read_text(encoding="utf-8").splitlines()][-1]


def test_the_audit_log_keeps_state_in_separate_named_fields(engine, chat):
    chat("I am diabetic and I take insulin. what can I eat for lunch")
    row = _last(engine)
    assert set(row["state"]) == {"emergency", "search_memory", "risk_flags", "diabetes", "language"}
    assert row["state"]["diabetes"]["status"] == "diabetic"
    assert row["state"]["risk_flags"].get("diabetes_medicine") == "yes"
    assert row["state"]["emergency"]["category"] is None
    assert row["operational_actions"] == []


def test_the_licence_switches_are_logged_once_per_line_in_the_versions_not_as_a_block(engine, chat):
    chat("what is a good lunch with dal")
    row = _last(engine)
    assert "licence" not in row
    assert "sources=usda" in row["versions"]["data_licence"] and "test_only_data=allowed" in row["versions"]["data_licence"]


def test_an_emergency_is_kept_in_the_emergency_field_only(engine, chat):
    chat("my throat is swelling after eating peanuts")
    state = _last(engine)["state"]
    assert state["emergency"]["category"] == "E3" and state["emergency"]["turn"] is not None
    assert state["diabetes"]["status"] == "unknown"
