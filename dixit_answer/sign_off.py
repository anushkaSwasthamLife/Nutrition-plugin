"""What is still unsigned before production (plan item F).

A limit is only usable in production if it says where it comes from, who reviewed it and when. This file holds those rules, so
the start-up check (consistency.py) and the readiness report (readiness.py) use the same ones.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

# Words that mean "nobody has filled this in". Compared in lower case after trimming.
EMPTY_WORDS = {"", "none", "null", "n/a", "na", "tbd", "tbc", "todo", "unknown", "-", "?"}
PLACEHOLDER_MARKS = ("placeholder", "test only", "tbd", "tbc", "todo", "lorem")
UNSIGNED_STATUS_MARKS = ("test only", "stub", "partial", "proposed", "placeholder", "draft", "unsigned", "pending")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def _text(value) -> str:
    """Trimmed text with invisible characters (zero-width marks) removed and look-alike letters (full-width) made plain."""
    if not isinstance(value, str):
        return ""
    plain = unicodedata.normalize("NFKC", value)
    return "".join(ch for ch in plain if unicodedata.category(ch) != "Cf").strip()


def _unfilled(value, minimum: int = 2) -> bool:
    """True when nothing real was written: empty, a stand-in word, or fewer than `minimum` letters or digits."""
    text = _text(value).lower()
    if text in EMPTY_WORDS or any(mark in text for mark in PLACEHOLDER_MARKS):
        return True
    return sum(ch.isalnum() for ch in text) < minimum


def _real_date(value, today: date) -> str | None:
    """None when the date is acceptable, otherwise the reason it is not."""
    text = _text(value)
    if not text:
        return "is missing"
    if not _DATE_RE.fullmatch(text):
        return f"'{value}' is not a date written YYYY-MM-DD"
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return f"'{value}' is not a date written YYYY-MM-DD"
    if parsed.year < 2000:
        return f"'{value}' is too long ago to be a real review"
    if parsed > today:
        return f"'{value}' is in the future"
    return None


def limit_problems(limits_cfg: dict, today: date | None = None) -> list[str]:
    """Every limit row that production must refuse, with the reason. Empty means every row is fully signed."""
    today = today or date.today()
    problems: list[str] = []
    rows = limits_cfg.get("limits")
    if not isinstance(rows, list) or not rows:
        return ["limits: the table has no rows"]
    seen: dict[tuple, int] = {}
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            problems.append(f"limits row {position}: is not a row")
            continue
        label = f"limits {row.get('condition')}/{row.get('nutrient')}"
        key = (_text(row.get("condition")).lower(), _text(row.get("nutrient")).lower())
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            problems.append(f"{label}: appears twice, so two reviewers could disagree; keep one row")
        if _unfilled(row.get("source"), 3):
            problems.append(f"{label}: has no real source (it says '{row.get('source')}')")
        if _unfilled(row.get("reviewer")):
            problems.append(f"{label}: has no reviewer")
        reason = _real_date(row.get("review_date"), today)
        if reason:
            problems.append(f"{label}: review_date {reason}")
    return problems


def file_problems(name: str, data: dict) -> list[str]:
    """Status and sign-off fields of one data file."""
    problems = []
    status = _text(data.get("status"))
    words = status.lower()
    if _unfilled(status, 3) or any(mark in words for mark in UNSIGNED_STATUS_MARKS):
        problems.append(f"{name}: status is '{status or 'missing'}'")
    return problems
