"""Edge cases for the audit report (plan item C): damaged logs, odd values, huge logs, bad arguments, zero thresholds."""
import copy
import json
import time

import pytest

from dixit_answer import audit_report as ar

RULES = ar.load_rules()


def turn(day="2026-10-10", **kw):
    base = {"kind": "turn", "at": f"{day}T10:00:00", "type": "recipe_request", "recipe_trace": [], "checks": [],
            "ai": {"provider": "mock", "fallback": False}, "emergency": None}
    base.update(kw)
    return base


def log(tmp_path, *lines, raw=None):
    path = tmp_path / "audit.jsonl"
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text("\n".join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- the file itself
def test_missing_empty_blank_and_folder(tmp_path, capsys):
    assert ar.main(["--file", str(tmp_path / "nope.jsonl"), "--date", "2026-10-10"]) == 0
    assert "No turns were logged" in capsys.readouterr().out
    assert ar.main(["--file", str(log(tmp_path, raw=b"")), "--date", "latest"]) == 0
    assert ar.main(["--file", str(log(tmp_path, raw=b"\n\n   \n\r\n")), "--date", "latest"]) == 0
    capsys.readouterr()
    assert ar.main(["--file", str(tmp_path)]) == 1
    assert "folder" in capsys.readouterr().err


def test_bom_crlf_invalid_utf8_and_a_half_written_last_line(tmp_path):
    good = json.dumps(turn()).encode("utf-8")
    raw = b"\xef\xbb\xbf" + good + b"\r\n" + b"\xff\xfe not json \x80\r\n" + good + b"\r\n" + good[:40]
    days, _, alerts, _ = ar.build(log(tmp_path, raw=raw), "2026-10-10", RULES)
    assert days["2026-10-10"].turns == 2 and not alerts


def test_unicode_and_huge_values_do_not_matter(tmp_path):
    big = turn(recipe_trace=["R9: no checked recipe; tips + recipe backlog" + "x" * 200_000], type="भोजन 🍛")
    days, _, _, _ = ar.build(log(tmp_path, big), "2026-10-10", RULES)
    assert days["2026-10-10"].recipe_fallbacks == 1


def test_a_big_log_is_read_quickly_and_streamed(tmp_path):
    rows = [json.dumps(turn(f"2026-10-{1 + i % 9:02d}", recipe_trace=["R4/R7: Dal: pass"])) for i in range(60_000)]
    path = log(tmp_path, *rows)
    started = time.perf_counter()
    days, day, alerts, _ = ar.build(path, "latest", RULES)
    assert sum(d.turns for d in days.values()) == 60_000 and time.perf_counter() - started < 20
    assert not hasattr(ar.read_records(path), "__len__")           # a generator, so the file is never held in memory


# ---------------------------------------------------------------- records with the wrong shape
@pytest.mark.parametrize("record", [
    {"kind": "turn"}, {"kind": "turn", "at": None}, {"kind": "turn", "at": 20261010}, {"kind": "turn", "at": ""},
    {"kind": "turn", "at": "garbage-garbage"}, {"kind": "turn", "at": "2026-13-45T10:00:00"}, {"kind": "turn", "at": "2026-02-30T10:00:00"},
    ["a", "list"], "a string", 42, None, {"at": "2026-10-10T10:00:00"}, {"kind": 5, "at": "2026-10-10T10:00:00"},
])
def test_records_without_a_real_date_or_kind_are_ignored_and_never_crash(tmp_path, record):
    days, day, alerts, _ = ar.build(log(tmp_path, record, turn()), "latest", RULES)
    assert list(days) == ["2026-10-10"] or (isinstance(record, dict) and record.get("at") == "2026-10-10T10:00:00")
    assert not alerts


@pytest.mark.parametrize("field, value", [
    ("recipe_trace", "R9: no checked recipe"), ("recipe_trace", {"a": 1}), ("recipe_trace", [1, None, ["x"], {"y": 2}]),
    ("checks", "text"), ("checks", {"level": "critical"}), ("checks", [1, "x", None, {"level": "critical"}, {"level": "critical", "passed": "no"}]),
    ("ai", "groq"), ("ai", ["x"]), ("ai", {"provider": ["x"], "fallback": 1}), ("emergency", "E1"), ("emergency", ["a"]),
    ("emergency", {"category": ["E1"], "negated": "chest pain"}), ("type", None), ("type", ["emergency"]),
])
def test_odd_field_values_never_crash(tmp_path, field, value):
    days, _, alerts, _ = ar.build(log(tmp_path, turn(**{field: value})), "2026-10-10", RULES)
    assert days["2026-10-10"].turns == 1
    json.dumps([a.as_dict() for a in alerts])


def test_a_recipe_name_with_a_colon_is_still_read_correctly(tmp_path):
    trace = ["R4/R7: Aloo: dry, spicy: pass", "R4/R7: Sabji: with: colons: ['energy_kcal over its limit']", "R5-R8: attempt 2: ['x']"]
    days, _, _, _ = ar.build(log(tmp_path, turn(recipe_trace=trace)), "2026-10-10", RULES)
    d = days["2026-10-10"]
    assert (d.gate_checks, d.gate_rejections, d.ai_attempts, d.ai_retries) == (3, 2, 1, 1)


def test_unknown_words_are_counted_even_with_apostrophes(tmp_path):
    trace = ["R1: unknown [\"ma's\", 'moringa', 'x y'], left out and queued for review", "R1: unknown [broken, left out"]
    days, _, _, _ = ar.build(log(tmp_path, turn(recipe_trace=trace)), "2026-10-10", RULES)
    assert days["2026-10-10"].unknown_words == 4          # three words, plus one for the line that cannot be read


# ---------------------------------------------------------------- days, baselines and arguments
def test_future_days_and_day_boundaries(tmp_path):
    rows = [turn("2026-10-09"), turn("2026-10-10"), turn("2027-01-01")]
    days, day, _, _ = ar.build(log(tmp_path, *rows), "latest", RULES)
    assert day == "2027-01-01" and set(days) == {"2026-10-09", "2026-10-10", "2027-01-01"}
    base = ar._baseline(days, "2026-10-10", 7)
    assert [b.day for b in base] == ["2026-10-09"]                      # nothing from the future is in the baseline


def test_a_day_with_no_data_and_a_date_far_in_the_past(tmp_path):
    days, day, alerts, _ = ar.build(log(tmp_path, turn()), "1999-01-01", RULES)
    assert day == "1999-01-01" and not alerts
    assert "No turns were logged" in ar.format_report(days, day, alerts, 7, RULES)


@pytest.mark.parametrize("bad", ["2026-02-30", "2026-13-01", "10/10/2026", "", " ", "yesterday", "TODAY", "2026-10-10T10:00", "٢٠٢٦-١٠-١٠"])
def test_bad_dates_are_a_clear_error(tmp_path, bad, capsys):
    assert ar.main(["--file", str(log(tmp_path, turn())), "--date", bad]) == 1
    assert "--date must be" in capsys.readouterr().err


@pytest.mark.parametrize("days", ["0", "-5", "1", "999"])
def test_days_argument_never_breaks_the_report(tmp_path, days, capsys):
    assert ar.main(["--file", str(log(tmp_path, turn("2026-10-09"), turn())), "--date", "latest", "--days", days]) == 0
    assert "Dixit answer report" in capsys.readouterr().out


def test_zero_thresholds_never_divide_by_zero(tmp_path):
    zero = copy.deepcopy(RULES)
    for rule in zero["rules"].values():
        for key in rule:
            if isinstance(rule[key], (int, float)) and not isinstance(rule[key], bool):
                rule[key] = 0
    days = ar.summarise(ar.read_records(log(tmp_path, turn(recipe_trace=["R4/R7: Dal: pass"]), turn("2026-10-09"))))
    alerts = ar.evaluate(days, "2026-10-10", zero)
    alerts += ar.evaluate(days, "2026-10-11", zero)                     # a day with nothing at all
    assert isinstance(alerts, list)


def test_only_one_day_of_data_has_no_baseline_and_cannot_cry_wolf(tmp_path):
    rows = [turn(recipe_trace=["R4/R7: Dal: pass"]) for _ in range(40)] + [turn(type="emergency", emergency={"category": "E1"})]
    assert not any(a.id == "rejections_fell" for a in ar.build(log(tmp_path, *rows), "2026-10-10", RULES)[2])


def test_the_json_output_is_always_valid_json_and_alerts_are_most_severe_first(tmp_path, capsys):
    bad = [{"check_id": "N1", "passed": False, "level": "critical", "detail": "x"}]
    rows = [turn(checks=bad), turn(type="emergency", emergency={"category": "E5", "matched": []})]
    assert ar.main(["--file", str(log(tmp_path, *rows)), "--date", "2026-10-10", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    order = [a["severity"] for a in data["alerts"]]
    assert order == sorted(order, key=lambda s: ar.SEVERITY_ORDER[s]) and {"critical", "notice"} <= set(order)
    assert ar.main(["--file", str(log(tmp_path, *rows)), "--date", "2026-10-10", "--fail-on-alert"]) == 2


def test_a_notice_alone_does_not_fail_a_scheduler(tmp_path):
    rows = [turn(type="emergency", emergency={"category": "E5", "matched": []})]
    assert ar.main(["--file", str(log(tmp_path, *rows)), "--date", "2026-10-10", "--fail-on-alert"]) == 0


def test_reading_the_live_audit_log_of_a_real_engine_turn_never_fails(engine, chat):
    chat("I have a seizure", "I have no chest pain, can I eat dal?", "what can I cook with xyzzy and aloo?")
    days, day, alerts, _ = ar.build(engine.settings.audit_log, "latest", RULES)
    assert day and isinstance(alerts, list)
