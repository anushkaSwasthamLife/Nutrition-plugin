"""When the approved sources do not cover a question, the gap is logged with its topic (audit kind knowledge_gap) and counted in the report."""
import json

import dixit_answer.pipeline as pipeline_module
from dixit_answer import audit_report
from dixit_answer.checks import CheckResult
from dixit_answer.pipeline import SAFE_FALLBACK


def gaps(engine, conversation_id):
    rows = [json.loads(line) for line in engine.settings.audit_log.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["kind"] == "knowledge_gap" and r["conversation_id"] == conversation_id]


def test_a_question_with_no_approved_guidance_is_logged_as_a_gap(engine, chat):
    r = chat("can I do intermittent fasting?")[0]
    assert r.type == "special_diet" and "don't have guidance" in r.answer
    rows = gaps(engine, r.conversation_id)
    assert len(rows) == 1 and rows[0]["topic"] == "special diet (fasting, keto)" and rows[0]["reason"] == "no approved guidance"
    assert rows[0]["plan_type"] == "special_diet" and rows[0]["turn"] == r.turn


def test_an_ordinary_answer_logs_no_gap(engine, chat):
    r = chat("protein in quinoa")[0]
    assert not gaps(engine, r.conversation_id)


def test_an_emergency_logs_no_gap(engine, chat):
    r = chat("my throat is swelling after eating peanuts")[0]
    assert r.type == "emergency" and not gaps(engine, r.conversation_id)


def test_the_safe_fallback_is_logged_with_the_failed_check(engine, chat, monkeypatch):
    real = pipeline_module.check_final

    def failing(final_text, draft, plan, *args, **kwargs):
        out = list(real(final_text, draft, plan, *args, **kwargs))
        if plan.type not in {"emergency", "harmful_intent"}:
            out.append(CheckResult("TESTFAIL", False, "critical", "forced for the test"))
        return out

    monkeypatch.setattr(pipeline_module, "check_final", failing)
    r = chat("what sabji can i eat with rice?")[0]
    assert r.answer == SAFE_FALLBACK and r.fallback
    rows = gaps(engine, r.conversation_id)
    assert len(rows) == 1 and rows[0]["topic"] == "food_choice_guidance" and "TESTFAIL" in rows[0]["reason"]


def test_the_report_counts_gaps_by_topic():
    records = [{"kind": "turn", "at": "2026-10-27T10:00:00", "type": "special_diet", "checks": []},
               {"kind": "knowledge_gap", "at": "2026-10-27T10:00:00", "topic": "special diet (fasting, keto)", "reason": "x"},
               {"kind": "knowledge_gap", "at": "2026-10-27T10:05:00", "topic": "special diet (fasting, keto)", "reason": "x"},
               {"kind": "knowledge_gap", "at": "2026-10-27T10:06:00", "topic": "protein: saffron", "reason": "no reliable value"},
               {"kind": "knowledge_gap", "at": "2026-10-27T10:07:00", "reason": "no topic"}]
    days = audit_report.summarise(records)
    day = days["2026-10-27"]
    assert day.knowledge_gaps == 4 and day.turns == 1
    assert day.gap_by_topic["special diet (fasting, keto)"] == 2 and day.gap_by_topic["unknown"] == 1
    assert day.as_dict()["knowledge_gaps"] == 4
    text = audit_report.format_report(days, "2026-10-27", [], 7, audit_report.load_rules())
    assert "Not covered by approved data  4" in text and "special diet (fasting, keto) 2" in text


def test_a_damaged_gap_record_does_not_crash_the_report():
    days = audit_report.summarise([{"kind": "knowledge_gap", "at": "2026-10-27T10:00:00", "topic": None}])
    assert days["2026-10-27"].knowledge_gaps == 1
