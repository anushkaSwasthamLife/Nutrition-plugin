"""Plan item C: daily report and alerts from the audit log."""
import json

import pytest

from dixit_answer import audit_report as ar

RULES = ar.load_rules()


def turn(day, *, type="recipe_request", trace=None, ai=None, checks=None, emergency=None, hour="10:00:00"):
    return {"kind": "turn", "at": f"{day}T{hour}", "type": type, "recipe_trace": trace or [],
            "ai": ai or {"provider": "mock", "used": False, "fallback": False}, "checks": checks or [], "emergency": emergency}


PASS = "R4/R7: Bhindi Sabji (dry): pass"
FAIL = "R4/R7: Bhindi Sabji (dry): ['energy_kcal over its limit']"
TIPS = "R9: no checked recipe; tips + recipe backlog"


def write(tmp_path, records):
    path = tmp_path / "audit.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def run(tmp_path, records, day):
    days, picked, alerts, _ = ar.build(write(tmp_path, records), day, RULES)
    return days, {a.id: a for a in alerts}


def test_counts_gate_checks_repairs_retries_and_fallbacks(tmp_path):
    records = [
        turn("2026-10-10", trace=[PASS + " after repairs ['ghee->oil']"]),
        turn("2026-10-10", trace=["R5-R8: attempt 1: ['too much sodium']", "R5-R8: attempt 2: pass"]),
        turn("2026-10-10", trace=[TIPS]),
        turn("2026-10-10", trace=["R9: partial approved match Dal, also need ['FOOD-ONION']"]),
        turn("2026-10-10", trace=["R5: draft failed (TimeoutError: slow)"]),
        turn("2026-10-10", type="greeting_thanks"),
    ]
    days, _ = run(tmp_path, records, "2026-10-10")
    d = days["2026-10-10"]
    assert (d.turns, d.recipe_turns) == (6, 5)
    assert (d.gate_checks, d.gate_rejections, d.repairs) == (3, 1, 1)
    assert (d.ai_attempts, d.ai_retries, d.ai_draft_failures) == (3, 1, 1)
    assert (d.recipe_fallbacks, d.partial_matches) == (1, 1)


def test_rejections_fell_fires_only_against_a_real_baseline(tmp_path):
    base = [turn(f"2026-10-0{d}", trace=[FAIL if i % 4 == 0 else PASS]) for d in range(1, 6) for i in range(8)]
    quiet = [turn("2026-10-10", trace=[PASS]) for _ in range(12)]
    _, alerts = run(tmp_path, base + quiet, "2026-10-10")
    assert alerts["rejections_fell"].severity == "critical"
    normal = [turn("2026-10-10", trace=[FAIL if i % 4 == 0 else PASS]) for i in range(12)]
    _, alerts = run(tmp_path, base + normal, "2026-10-10")
    assert "rejections_fell" not in alerts
    _, alerts = run(tmp_path, quiet, "2026-10-10")           # no history: never guess
    assert "rejections_fell" not in alerts


def emergency(day, cat, n=1):
    return [turn(day, type="emergency", emergency={"category": cat, "matched": [], "negated": []}) for _ in range(n)]


def test_emergency_spike_and_self_harm_notice(tmp_path):
    base = [r for d in range(1, 6) for r in emergency(f"2026-10-0{d}", "E1") + [turn(f"2026-10-0{d}")]]
    _, alerts = run(tmp_path, base + emergency("2026-10-10", "E2", 6), "2026-10-10")
    assert alerts["emergency_spike"].numbers["today"] == 6 and "self_harm_seen" not in alerts
    _, alerts = run(tmp_path, base + emergency("2026-10-10", "E5"), "2026-10-10")
    assert alerts["self_harm_seen"].severity == "notice" and "emergency_spike" not in alerts


def test_fallbacks_rose(tmp_path):
    base = [turn(f"2026-10-0{d}", trace=[PASS]) for d in range(1, 4) for _ in range(5)]
    bad = [turn("2026-10-10", trace=[TIPS]) for _ in range(3)] + [turn("2026-10-10", trace=[PASS]) for _ in range(3)]
    _, alerts = run(tmp_path, base + bad, "2026-10-10")
    assert "fallbacks_rose" in alerts
    few = [turn("2026-10-10", trace=[TIPS]) for _ in range(2)]            # too few turns to say anything
    _, alerts = run(tmp_path, base + few, "2026-10-10")
    assert "fallbacks_rose" not in alerts


def test_wording_fallbacks_rose(tmp_path):
    ai = lambda fb: {"provider": "groq", "used": not fb, "fallback": fb}      # noqa: E731
    records = [turn("2026-10-10", ai=ai(i < 4)) for i in range(12)]
    _, alerts = run(tmp_path, records, "2026-10-10")
    assert alerts["wording_fallbacks_rose"].numbers["rate"] == pytest.approx(4 / 12, abs=0.001)
    _, alerts = run(tmp_path, [turn("2026-10-10", ai=ai(False)) for _ in range(12)], "2026-10-10")
    assert "wording_fallbacks_rose" not in alerts


def test_critical_check_failure_is_critical_and_emergency_turns_are_not_counted(tmp_path):
    bad = [{"check_id": "N1", "passed": False, "level": "critical", "detail": "x"}]
    _, alerts = run(tmp_path, [turn("2026-10-10", checks=bad)], "2026-10-10")
    assert alerts["critical_check_failed"].severity == "critical"
    _, alerts = run(tmp_path, [turn("2026-10-10", type="emergency", checks=bad, emergency={"category": "E1"})], "2026-10-10")
    assert "critical_check_failed" not in alerts


def test_negated_mentions_and_feedback_and_bad_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    lines = [json.dumps(turn("2026-10-10", emergency={"category": "", "negated": ["chest pain"], "matched": []})),
             json.dumps({"kind": "feedback", "at": "2026-10-10T11:00:00", "feedback": "helpful"}),
             json.dumps({"kind": "feedback", "at": "2026-10-10T11:01:00", "feedback": "something_wrong"}),
             "{not json", "", json.dumps([1, 2]), json.dumps({"kind": "turn"})]
    path.write_text("\n".join(lines), encoding="utf-8")
    days, _, alerts, _ = ar.build(path, "2026-10-10", RULES)
    d = days["2026-10-10"]
    assert (d.negated_mentions, d.feedback_helpful, d.feedback_problem, d.turns) == (1, 1, 1, 1) and not alerts


def test_text_and_json_output_and_exit_codes(tmp_path, capsys):
    bad = [{"check_id": "N1", "passed": False, "level": "critical", "detail": "x"}]
    path = write(tmp_path, [turn("2026-10-10", checks=bad)])
    assert ar.main(["--file", str(path), "--date", "2026-10-10", "--fail-on-alert"]) == 2
    text = capsys.readouterr().out
    assert "[CRITICAL] critical_check_failed" in text and "Dixit answer report for 2026-10-10" in text
    assert ar.main(["--file", str(path), "--date", "latest", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["alerts"][0]["id"] == "critical_check_failed" and data["today"]["turns"] == 1
    assert ar.main(["--file", str(path), "--date", "tomorrow"]) == 1
    assert ar.main(["--file", str(tmp_path / "missing.jsonl"), "--date", "2026-10-10", "--fail-on-alert"]) == 0


def test_a_real_engine_turn_is_counted(engine, chat):
    chat("what can I cook with aloo and onion?", "I have no chest pain, can I eat dal?", "I am shaking and sweating")
    days = ar.summarise(ar.read_records(engine.settings.audit_log))
    total = sum(d.turns for d in days.values())
    assert total >= 3 and sum(d.emergency_replies for d in days.values()) >= 1
    assert sum(d.negated_mentions for d in days.values()) >= 1


def test_alert_rules_file_is_complete():
    assert RULES["status"] == "PROPOSED" and set(RULES["rules"]) == {
        "rejections_fell", "emergency_spike", "self_harm_seen", "fallbacks_rose", "wording_fallbacks_rose", "critical_check_failed"}
    assert all(r["severity"] in ar.SEVERITY_ORDER and r["what"] for r in RULES["rules"].values())
