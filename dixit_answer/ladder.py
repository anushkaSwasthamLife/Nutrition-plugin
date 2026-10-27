"""The authority ladder (data/authority_ladder.json): which source wins when two disagree. Advisory today; see the file's note.

Used for three things: a start-up check that every rule in rules_registry.json has a place on the ladder, a lookup for tests and
reviewers (`stricter`), and the version that goes into every audit record.
"""
from __future__ import annotations


class Ladder:
    def __init__(self, data: dict):
        self.version = str(data.get("version", ""))
        self.status = str(data.get("status", ""))
        self.rungs = sorted((r for r in data.get("rungs", []) if isinstance(r, dict) and isinstance(r.get("rung"), int)),
                            key=lambda r: r["rung"])
        self.universal = set(data.get("universal") or [])
        self._rung_of = {rule: r["rung"] for r in self.rungs for rule in r.get("registry_rules", [])}

    def rung_of(self, rule_id: str) -> int | None:
        """The rung of a registry rule, or None for a universal rule (applies on every rung) or an unknown id."""
        return self._rung_of.get(rule_id)

    def stricter(self, a: str, b: str) -> str | None:
        """Of two registry rules, the one higher on the ladder (the lower rung number). None if either has no rung or they share one."""
        ra, rb = self.rung_of(a), self.rung_of(b)
        if ra is None or rb is None or ra == rb:
            return None
        return a if ra < rb else b


def problems(data: dict | None, registry: dict) -> list[str]:
    """Reasons the ladder does not fit the registry. Empty means it does."""
    if not isinstance(data, dict):
        return ["authority ladder: the file is missing"]
    out: list[str] = []
    rungs = [r for r in data.get("rungs", []) if isinstance(r, dict)]
    numbers = [r.get("rung") for r in rungs]
    if numbers != list(range(1, len(rungs) + 1)):
        out.append(f"authority ladder: rungs must be numbered 1 to {len(rungs)} in order (got {numbers})")
    for r in rungs:
        for field in ("id", "name", "controls", "must_never"):
            if not str(r.get(field) or "").strip():
                out.append(f"authority ladder: rung {r.get('rung')} has no {field}")
    ids = [r.get("id") for r in rungs]
    if len(ids) != len(set(ids)):
        out.append("authority ladder: two rungs share an id")
    if rungs and rungs[-1].get("id") != "MODEL_KNOWLEDGE":
        out.append("authority ladder: general model knowledge must be the last rung (it may only phrase, never decide)")
    placed: dict[str, int] = {}
    for r in rungs:
        for rule in r.get("registry_rules", []):
            placed[rule] = placed.get(rule, 0) + 1
    universal = list(data.get("universal") or [])
    for rule in universal:
        placed[rule] = placed.get(rule, 0) + 1
    known = {r["id"] for r in registry.get("order", []) if isinstance(r, dict) and "id" in r}
    for rule in sorted(known - set(placed)):
        out.append(f"authority ladder: registry rule {rule} is on no rung and not marked universal")
    for rule, count in sorted(placed.items()):
        if rule not in known:
            out.append(f"authority ladder: {rule} is not in rules_registry.json")
        elif count > 1:
            out.append(f"authority ladder: registry rule {rule} appears {count} times")
    return out
