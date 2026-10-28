"""Daily report and alerts from the append-only audit log (plan item C).

Run:  .venv\\Scripts\\python.exe -m dixit_answer.audit_report [--date today|latest|YYYY-MM-DD] [--days 7] [--json] [--fail-on-alert]

Reads var/audit.jsonl, counts what happened per day, and compares the chosen day with the average of the earlier days that
have data. Thresholds live in data/alert_rules.json (PROPOSED: Swastham confirms them). Nothing here changes an answer.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import DATA_DIR, load_settings

RULES_PATH = DATA_DIR / "alert_rules.json"
SEVERITY_ORDER = {"critical": 0, "warning": 1, "notice": 2}
COUNT_FIELDS = ("turns", "recipe_turns", "gate_checks", "gate_rejections", "repairs", "ai_attempts", "ai_retries",
                "ai_draft_failures", "recipe_fallbacks", "partial_matches", "ai_wording_turns", "wording_fallbacks",
                "critical_check_failures", "emergency_replies", "harmful_replies", "negated_mentions", "unknown_words", "feedback_helpful",
                "feedback_problem")


@dataclass
class DayStats:
    day: str
    turns: int = 0
    recipe_turns: int = 0                 # turns that ran the recipe flow
    gate_checks: int = 0                  # recipes put through the gate (approved or AI-drafted, each attempt)
    gate_rejections: int = 0              # of those, how many the gate rejected
    repairs: int = 0                      # checks that needed a repair swap
    ai_attempts: int = 0                  # AI recipe drafts asked for
    ai_retries: int = 0                   # of those, second or third attempts
    ai_draft_failures: int = 0            # provider errors
    recipe_fallbacks: int = 0             # recipe requests that ended with tips and no checked recipe
    partial_matches: int = 0              # answered with a partly matching approved recipe
    ai_wording_turns: int = 0             # turns where the AI rewording was tried
    wording_fallbacks: int = 0            # of those, the plain template was sent instead
    critical_check_failures: int = 0      # turns where even the template failed a critical check
    emergency_replies: int = 0
    emergency_by_category: Counter = field(default_factory=Counter)
    harmful_replies: int = 0              # guardrail A3: starving, purging, diet pills, unsafe speed
    harmful_by_category: Counter = field(default_factory=Counter)
    negated_mentions: int = 0             # a symptom was mentioned with "no" in front
    unknown_words: int = 0                # food words we did not recognise
    knowledge_gaps: int = 0               # questions the approved sources did not cover (no approved guidance or no reliable value)
    gap_by_topic: Counter = field(default_factory=Counter)
    feedback_helpful: int = 0
    feedback_problem: int = 0

    def as_dict(self) -> dict:
        out = {k: getattr(self, k) for k in ("day", *COUNT_FIELDS)}
        out["emergency_by_category"] = dict(sorted(self.emergency_by_category.items()))
        out["harmful_by_category"] = dict(sorted(self.harmful_by_category.items()))
        out["knowledge_gaps"] = self.knowledge_gaps
        out["gap_by_topic"] = dict(sorted(self.gap_by_topic.items()))
        return out


@dataclass
class Alert:
    id: str
    severity: str
    message: str
    numbers: dict

    def as_dict(self) -> dict:
        return {"id": self.id, "severity": self.severity, "message": self.message, "numbers": self.numbers}


def load_rules(path: Path | None = None) -> dict:
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def read_records(path: Path):
    """Every readable line of the log, one at a time. A damaged line, a record without a real date, or a missing file never stops the report."""
    if not path.is_file():
        return
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and isinstance(record.get("at"), str) and _DAY_RE.match(record["at"]):
                try:
                    datetime.strptime(record["at"][:10], "%Y-%m-%d")
                except ValueError:
                    continue
                yield record


_ATTEMPT_RE = re.compile(r"attempt (\d+)")
_RESULT_RE = re.compile(r": (pass\b.*|\[.*)$")


def _count_trace(stats: DayStats, trace) -> None:
    for line in trace if isinstance(trace, list) else []:
        if not isinstance(line, str):
            continue
        if line.startswith(("R4/R7:", "R5-R8:")):
            found = _RESULT_RE.search(line)
            stats.gate_checks += 1
            if not (found and found.group(1).startswith("pass")):
                stats.gate_rejections += 1
            if " after repairs " in line:
                stats.repairs += 1
        if line.startswith(("R5-R8: attempt", "R5: attempt", "R5: draft failed")):
            stats.ai_attempts += 1
            match = _ATTEMPT_RE.search(line)
            if match and int(match.group(1)) >= 2:
                stats.ai_retries += 1
            if line.startswith("R5: draft failed"):
                stats.ai_draft_failures += 1
        if line.startswith("R9: no checked recipe"):
            stats.recipe_fallbacks += 1
        if line.startswith("R9: partial approved match"):
            stats.partial_matches += 1
        if line.startswith("R1: unknown ["):
            try:
                words = ast.literal_eval(line[len("R1: unknown "):line.index("],") + 1])
                stats.unknown_words += len(words) if isinstance(words, list) else 1
            except (ValueError, SyntaxError):
                stats.unknown_words += 1


def summarise(records) -> dict[str, DayStats]:
    days: dict[str, DayStats] = {}
    for record in records:
        day = record["at"][:10]
        stats = days.setdefault(day, DayStats(day))
        kind = record.get("kind")
        if kind == "feedback":
            if record.get("feedback") == "helpful":
                stats.feedback_helpful += 1
            else:
                stats.feedback_problem += 1
            continue
        if kind == "unknown_item":
            continue                        # counted from the turn's trace, so one word is not counted twice
        if kind == "knowledge_gap":
            stats.knowledge_gaps += 1
            stats.gap_by_topic[str(record.get("topic") or "unknown")[:60]] += 1
            continue
        if kind != "turn":
            continue
        stats.turns += 1
        trace = record.get("recipe_trace")
        if trace:
            stats.recipe_turns += 1
            _count_trace(stats, trace)
        ai = record.get("ai") if isinstance(record.get("ai"), dict) else {}
        if ai.get("provider") not in (None, "", "mock") and record.get("type") not in {"emergency", "harmful_intent"}:
            stats.ai_wording_turns += 1
            if ai.get("fallback"):
                stats.wording_fallbacks += 1
        checks = record.get("checks") if isinstance(record.get("checks"), list) else []
        if any(isinstance(c, dict) and c.get("level") == "critical" and c.get("passed") is False for c in checks) \
                and record.get("type") not in {"emergency", "harmful_intent"}:
            stats.critical_check_failures += 1
        em = record.get("emergency") if isinstance(record.get("emergency"), dict) else None
        if record.get("type") == "emergency":
            stats.emergency_replies += 1
            stats.emergency_by_category[str((em or {}).get("category") or "unknown")] += 1
        elif em and em.get("negated"):
            stats.negated_mentions += 1
        if record.get("type") == "harmful_intent":
            harm = record.get("harmful") if isinstance(record.get("harmful"), dict) else {}
            stats.harmful_replies += 1
            stats.harmful_by_category[str(harm.get("category") or "unknown")] += 1
    return days


def _baseline(days: dict[str, DayStats], day: str, n: int) -> list[DayStats]:
    earlier = sorted((d for d in days if d < day), reverse=True)[:n]
    return [days[d] for d in earlier]


def _sum(items: list[DayStats], name: str) -> int:
    return sum(getattr(i, name) for i in items)


def evaluate(days: dict[str, DayStats], day: str, rules: dict) -> list[Alert]:
    today = days.get(day, DayStats(day))
    base = _baseline(days, day, int(rules["baseline_days"]))
    r = rules["rules"]
    alerts: list[Alert] = []

    def add(rule_id: str, message: str, **numbers) -> None:
        alerts.append(Alert(rule_id, r[rule_id]["severity"], message, numbers))

    # 1 The gate rejects far fewer recipes than usual.
    rule = r["rejections_fell"]
    base_checks, base_rej = _sum(base, "gate_checks"), _sum(base, "gate_rejections")
    if base_checks >= max(1, rule["min_baseline_checks"]) and today.gate_checks >= max(1, rule["min_today_checks"]):
        base_rate, today_rate = base_rej / base_checks, today.gate_rejections / today.gate_checks
        if base_rate >= rule["min_baseline_rate"] and today_rate < base_rate * rule["today_below_fraction_of_baseline"]:
            add("rejections_fell",
                f"The gate rejected {today.gate_rejections} of {today.gate_checks} recipes today ({today_rate:.0%}), against "
                f"{base_rate:.0%} on the earlier days. It may have stopped checking properly. Look at today's recipe turns.",
                today_rate=round(today_rate, 3), baseline_rate=round(base_rate, 3), today_checks=today.gate_checks)

    # 2 Emergency replies spike.
    rule = r["emergency_spike"]
    base_days = [b for b in base if b.turns]
    base_avg = (_sum(base_days, "emergency_replies") / len(base_days)) if base_days else 0.0
    if today.emergency_replies >= max(rule["min_count"], rule["times_baseline"] * base_avg):
        add("emergency_spike",
            f"{today.emergency_replies} emergency replies today, against an average of {base_avg:.1f} a day before. "
            "Check whether a wording change is triggering the emergency check by mistake, or whether something has happened.",
            today=today.emergency_replies, baseline_average=round(base_avg, 2))

    # 3 Self-harm reply sent: always shown, so a person looks at it.
    rule = r["self_harm_seen"]
    if today.emergency_by_category.get("E5", 0) >= rule["min_count"]:
        add("self_harm_seen",
            f"{today.emergency_by_category['E5']} self-harm reply(ies) were sent today. A trained person should read these "
            "turns in the audit log and decide whether any follow-up is needed.",
            count=today.emergency_by_category["E5"])

    # 3b Starving or purging reply sent (H1, H2): always shown, like self-harm. Older rule files without the rule skip it.
    rule = r.get("harmful_intent_seen")
    if rule:
        count = sum(today.harmful_by_category.get(c, 0) for c in rule["categories"])
        if count >= rule["min_count"]:
            add("harmful_intent_seen",
                f"{count} starving or purging reply(ies) were sent today. A trained person should read these turns in the "
                "audit log and decide whether any follow-up is needed.", count=count)

    # 4 More recipe requests end with no checked recipe.
    rule = r["fallbacks_rose"]
    if today.recipe_turns >= max(1, rule["min_recipe_turns"]):
        today_rate = today.recipe_fallbacks / today.recipe_turns
        base_turns = _sum(base, "recipe_turns")
        base_rate = (_sum(base, "recipe_fallbacks") / base_turns) if base_turns else 0.0
        if today_rate >= rule["min_rate"] and today_rate >= base_rate * rule["times_baseline"]:
            add("fallbacks_rose",
                f"{today.recipe_fallbacks} of {today.recipe_turns} recipe requests ended with no checked recipe today "
                f"({today_rate:.0%}), against {base_rate:.0%} before. Common causes: foods missing from the food list, "
                "limits that cannot be checked, or the AI provider failing.",
                today_rate=round(today_rate, 3), baseline_rate=round(base_rate, 3))

    # 5 The AI's wording keeps failing the checks.
    rule = r["wording_fallbacks_rose"]
    if today.ai_wording_turns >= max(1, rule["min_ai_turns"]):
        rate = today.wording_fallbacks / today.ai_wording_turns
        if rate >= rule["min_rate"]:
            add("wording_fallbacks_rose",
                f"The plain template was sent instead of the AI's wording on {today.wording_fallbacks} of "
                f"{today.ai_wording_turns} answers today ({rate:.0%}). The AI model or its prompt may have changed.",
                rate=round(rate, 3))

    # 6 Even the template broke a critical check.
    rule = r["critical_check_failed"]
    if today.critical_check_failures >= rule["min_count"]:
        add("critical_check_failed",
            f"A critical answer check failed on {today.critical_check_failures} turn(s) today and the safe fallback text was "
            "sent. Open those turns in the audit log: the cause is in the 'checks' field.",
            count=today.critical_check_failures)

    alerts.sort(key=lambda a: SEVERITY_ORDER[a.severity])
    return alerts


def resolve_day(arg: str, days: dict[str, DayStats]) -> str:
    if arg == "today":
        return date.today().isoformat()
    if arg == "latest":
        return max(days) if days else date.today().isoformat()
    datetime.strptime(arg, "%Y-%m-%d")       # raises ValueError for anything else
    return arg


def format_report(days: dict[str, DayStats], day: str, alerts: list[Alert], shown_days: int, rules: dict) -> str:
    today = days.get(day, DayStats(day))
    base = _baseline(days, day, int(rules["baseline_days"]))
    lines = [f"Dixit answer report for {day}",
             f"Compared with the average of {len(base)} earlier day(s) with data (thresholds {rules['version']}, {rules['status']})", ""]
    if not today.turns:
        lines.append("No turns were logged on this day.")
        lines.append("")
    lines += ["Counts for this day",
              f"  Turns                         {today.turns}",
              f"  Recipe requests               {today.recipe_turns}",
              f"  Recipes put through the gate  {today.gate_checks}  (rejected {today.gate_rejections}, needed a repair {today.repairs})",
              f"  AI recipe drafts              {today.ai_attempts}  (retries {today.ai_retries}, provider errors {today.ai_draft_failures})",
              f"  No checked recipe (tips)      {today.recipe_fallbacks}   partly matching recipe {today.partial_matches}",
              f"  AI wording tried              {today.ai_wording_turns}  (plain template sent instead {today.wording_fallbacks})",
              f"  Critical check failures       {today.critical_check_failures}",
              f"  Emergency replies             {today.emergency_replies}  " +
              (", ".join(f"{k} {v}" for k, v in sorted(today.emergency_by_category.items())) or "-"),
              f"  Harmful-intent replies        {today.harmful_replies}  " +
              (", ".join(f"{k} {v}" for k, v in sorted(today.harmful_by_category.items())) or "-"),
              f"  Symptom mentioned with 'no'   {today.negated_mentions}",
              f"  Unknown food words            {today.unknown_words}",
              f"  Not covered by approved data  {today.knowledge_gaps}  " +
              (", ".join(f"{k} {v}" for k, v in today.gap_by_topic.most_common(5)) or "-"),
              f"  Feedback                      helpful {today.feedback_helpful}, problem {today.feedback_problem}", ""]
    recent = sorted(days)[-shown_days:]
    if len(recent) > 1:
        lines.append(f"Last {len(recent)} day(s) with data")
        lines.append("  day         turns  recipes  rejected/checked  fallbacks  emergencies")
        for d in recent:
            s = days[d]
            lines.append(f"  {d}  {s.turns:5d}  {s.recipe_turns:7d}  {s.gate_rejections:>8}/{s.gate_checks:<7}  "
                         f"{s.recipe_fallbacks:9d}  {s.emergency_replies:11d}")
        lines.append("")
    if alerts:
        lines.append(f"ALERTS ({len(alerts)})")
        for a in alerts:
            lines.append(f"  [{a.severity.upper()}] {a.id}")
            lines.append(f"      {a.message}")
    else:
        lines.append("No alerts.")
    return "\n".join(lines)


def build(path: Path, day_arg: str = "latest", rules: dict | None = None) -> tuple[dict[str, DayStats], str, list[Alert], dict]:
    rules = rules or load_rules()
    days = summarise(read_records(path))
    day = resolve_day(day_arg, days)
    return days, day, evaluate(days, day, rules), rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily report and alerts from the Dixit audit log.")
    parser.add_argument("--file", type=Path, default=None, help="audit log (default: var/audit.jsonl)")
    parser.add_argument("--date", default="today", help="today, latest (the last day in the log) or YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=7, help="how many recent days to list")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--fail-on-alert", action="store_true", help="exit with code 2 when a critical or warning alert fires")
    args = parser.parse_args(argv)
    try:
        path = args.file or load_settings().audit_log
        if path.is_dir():
            print(f"{path} is a folder, not an audit log file.", file=sys.stderr)
            return 1
        days, day, alerts, rules = build(path, args.date)
    except ValueError:
        print("--date must be today, latest or YYYY-MM-DD", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Cannot read the audit log: {exc}", file=sys.stderr)
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.json:
        print(json.dumps({"day": day, "today": days.get(day, DayStats(day)).as_dict(), "alerts": [a.as_dict() for a in alerts],
                          "days": [days[d].as_dict() for d in sorted(days)[-max(args.days, 1):]]}, indent=2))
    else:
        print(format_report(days, day, alerts, max(args.days, 1), rules))
    if args.fail_on_alert and any(a.severity in ("critical", "warning") for a in alerts):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
