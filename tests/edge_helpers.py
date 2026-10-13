"""Shared invariants for the edge tests: whatever goes in, what comes out must be safe and well formed."""
import re

BAD_TOKENS = re.compile(r"\b(None|nan|inf|undefined|Traceback)\b|<\s*/?\s*[a-zA-Z]|javascript:|https?://|www\.|\{\{|\}\}")
EXCLUSION_MARKERS = ("left out", "won't suit", "can't show", "instead of", "Adjusted for you", "isn't suitable", "isn't in our food list",
                     "because of")


def assert_valid(result, label=""):
    """The answer is non-empty text with no placeholders, markup, links or leaked internals, and no unsent critical failure."""
    answer = result.answer
    assert isinstance(answer, str) and answer.strip(), f"empty answer {label}"
    bad = BAD_TOKENS.search(answer)
    assert not bad, f"unsafe or leaked token {bad.group(0)!r} in answer {label}: {answer[:300]!r}"
    assert len(answer) < 6000, f"answer too long ({len(answer)}) {label}"
    critical = [c for c in result.checks if not c["passed"] and c["level"] == "critical"]
    assert not critical or result.fallback, f"critical check failed but answer was sent {label}: {critical}"
    assert result.turn >= 0


def lines_naming_blocked_foods(engine, answer, blocked):
    """Lines (other than 'left out / won't suit' notes) that name a food this person must never be offered."""
    bad = []
    for line in answer.splitlines():
        if any(marker in line for marker in EXCLUSION_MARKERS):
            continue
        named = {m.food_id for m in engine.index.find(line)}
        if named & blocked:
            bad.append((line, sorted(named & blocked)))
    return bad
