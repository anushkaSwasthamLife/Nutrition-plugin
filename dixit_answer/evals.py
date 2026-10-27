"""The eval set runner: scores a chat pipeline on `evals/eval_set.json`.

    python -m dixit_answer.evals                       # the current pipeline (rules), every conversation
    python -m dixit_answer.evals --area emergency -v   # one area, with every failed check
    python -m dixit_answer.evals --out report.json     # also write the full report

Each conversation is sent turn by turn to a target; each turn's answer is turned into an Observation (what the person saw, which
safety label it was given, who handled it, what kind of answer it was) and checked against the turn's expectations. The checks
are the same for any target, so the rules and the coming LLM pipeline are scored on the same cases. A rubric is kept for the LLM
judge; until a judge is set it is counted as "not judged", never as passed. A failed check on a critical turn fails the run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

EVAL_SET = Path(__file__).resolve().parent.parent / "evals" / "eval_set.json"
EXPECT_KEYS = {"safety", "handled_by", "kind", "must_contain", "must_contain_any", "must_not_contain", "must_not_match", "rubric",
               "critical"}

# What the current pipeline's categories mean as eval safety labels.
CATEGORY_LABELS = {"E1": "emergency_low_sugar", "E2": "emergency_heart_stroke", "E3": "emergency_breathing_allergy",
                   "E4": "emergency_high_sugar", "E5": "self_harm", "E6": "emergency_collapse_poison", "E7": "emergency_pregnancy",
                   "H1": "purging", "H2": "starving", "H3": "diet_pills", "H4": "unsafe_weight_loss", "H5": "harm_to_others"}
ROUTE_LABELS = {"medicine": "medicine_advice", "cure": "cure_claim", "child": "child_diet"}
FACT_TYPES = {"neutral_nutrient_lookup", "phrase_comparison", "neutral_food_comparison", "target_meal_search", "source_fact_lookup"}
RECIPE_TYPES = {"recipe_request", "meal_from_ingredients"}
ACK_TYPES = {"profile_statement", "followup_reply", "safety_ack"}
FIXED_KINDS = {"emergency": "emergency", "harmful_intent": "safety_refusal", "main_ai": "fixed_reply", "clarify": "clarify",
               "invalid_message": "invalid", "out_of_scope": "out_of_scope"}


@dataclass
class Observation:
    answer: str
    safety: str = "none"            # one of the eval set's safety labels
    handled_by: str = ""            # safety | main | plugin | router
    kind: str = ""                  # one of the eval set's kinds
    detail: dict = field(default_factory=dict)      # target-specific extras for the report (type, route)


class RulesTarget:
    """The current pipeline: emergency and harmful-intent rules, the intent router, the nutrition plugin."""
    name = "rules"

    def __init__(self, main=None):
        if main is None:
            from .config import load_settings
            from .llm import MockLLM
            from .main_pipeline import MainPipeline
            from .pipeline import Engine
            settings = load_settings(llm_provider="mock", llm_api_key="", var_dir=Path(tempfile.mkdtemp(prefix="dixit-evals-")))
            main = MainPipeline(Engine(settings, llm=MockLLM()))
        self.main = main

    def send(self, message: str, conversation_id: str | None, profile: dict | None, doctor_id: str | None):
        result = self.main.answer(message, conversation_id, profile, doctor_id=doctor_id)
        return result.conversation_id, self.observe(result)

    @staticmethod
    def observe(result) -> Observation:
        plan = result.plan or {}
        route = result.route or {}
        if result.type in {"emergency", "harmful_intent"}:
            safety = CATEGORY_LABELS.get(plan.get("emergency_category", ""), "none")
        else:
            safety = ROUTE_LABELS.get(route.get("intent", ""), "none")
        kind = FIXED_KINDS.get(result.type)
        if kind is None:
            if result.type in FACT_TYPES:
                kind = "nutrition_fact"
            elif result.type in RECIPE_TYPES:
                kind = "recipe" if "**You need**" in result.answer else "recipe_list"
            elif result.type in ACK_TYPES:
                kind = "acknowledgement"
            else:
                kind = "food_guidance"
        return Observation(result.answer, safety, result.handled_by or "", kind,
                           {"type": result.type, "route": route.get("intent", "")})


def check_turn(expect: dict, obs: Observation) -> list[dict]:
    """Every check the turn asks for: [{"check", "passed", "detail"}]. A rubric is "not judged" (passed None) without a judge."""
    out = []
    low = obs.answer.lower()

    def add(check: str, passed, detail: str = "") -> None:
        out.append({"check": check, "passed": passed, "detail": detail})

    if "safety" in expect:
        wanted = expect["safety"] if isinstance(expect["safety"], list) else [expect["safety"]]
        add("safety", obs.safety in wanted, f"expected {' or '.join(wanted)}, got {obs.safety}")
    if "handled_by" in expect:
        add("handled_by", obs.handled_by == expect["handled_by"], f"expected {expect['handled_by']}, got {obs.handled_by}")
    if "kind" in expect:
        add("kind", obs.kind == expect["kind"], f"expected {expect['kind']}, got {obs.kind}")
    for phrase in expect.get("must_contain", []):
        add("must_contain", phrase.lower() in low, phrase)
    if expect.get("must_contain_any"):
        add("must_contain_any", any(p.lower() in low for p in expect["must_contain_any"]), " | ".join(expect["must_contain_any"]))
    for phrase in expect.get("must_not_contain", []):
        add("must_not_contain", phrase.lower() not in low, phrase)
    for pattern in expect.get("must_not_match", []):
        add("must_not_match", not re.search(pattern, obs.answer, re.IGNORECASE), pattern)
    if expect.get("rubric"):
        add("rubric", None, expect["rubric"])
    return out


def load(path: Path = EVAL_SET) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def problems(data: dict) -> list[str]:
    """The eval set's own mistakes: duplicate ids, unknown labels, kinds, profiles or expectation keys, a bad pattern."""
    out, seen = [], set()
    labels, kinds, handlers = set(data["safety_labels"]), set(data["kinds"]), set(data["handled_by"])
    for conv in data["conversations"]:
        cid = conv.get("id", "?")
        if cid in seen:
            out.append(f"{cid}: duplicate id")
        seen.add(cid)
        if conv.get("profile") not in data["profiles"]:
            out.append(f"{cid}: unknown profile {conv.get('profile')!r}")
        if not conv.get("turns"):
            out.append(f"{cid}: no turns")
        for i, turn in enumerate(conv.get("turns", []), 1):
            where, expect = f"{cid} turn {i}", turn.get("expect", {})
            if not isinstance(turn.get("message"), str):
                out.append(f"{where}: no message")
            out += [f"{where}: unknown expectation {k!r}" for k in set(expect) - EXPECT_KEYS]
            wanted = expect.get("safety", [])
            out += [f"{where}: unknown safety label {s!r}" for s in (wanted if isinstance(wanted, list) else [wanted]) if s not in labels]
            if "kind" in expect and expect["kind"] not in kinds:
                out.append(f"{where}: unknown kind {expect['kind']!r}")
            if "handled_by" in expect and expect["handled_by"] not in handlers:
                out.append(f"{where}: unknown handled_by {expect['handled_by']!r}")
            for pattern in expect.get("must_not_match", []):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    out.append(f"{where}: bad pattern {pattern!r} ({exc})")
    return out


def run(data: dict, target, ids=None, areas=None) -> dict:
    """Send every chosen conversation to the target and check every turn. Returns the report."""
    found = problems(data)
    if found:
        raise ValueError("eval set problems:\n" + "\n".join(found))
    turns = []
    for conv in data["conversations"]:
        if (ids and conv["id"] not in ids) or (areas and conv["area"] not in areas):
            continue
        profile, cid = data["profiles"][conv["profile"]], None
        for i, turn in enumerate(conv["turns"], 1):
            cid, obs = target.send(turn["message"], cid, profile, conv.get("doctor_id"))
            checks = check_turn(turn["expect"], obs)
            turns.append({"id": f"{conv['id']}.{i}", "area": conv["area"], "tags": conv.get("tags", []), "message": turn["message"],
                          "critical": bool(turn["expect"].get("critical")), "expected_safety": turn["expect"].get("safety"),
                          "observed": asdict(obs), "checks": checks,
                          "passed": all(c["passed"] is not False for c in checks)})
    return {"eval_set": data["version"], "target": target.name, "summary": summarise(turns), "turns": turns}


def summarise(turns: list[dict]) -> dict:
    by_area: dict = defaultdict(lambda: {"turns": 0, "passed": 0})
    safety = defaultdict(Counter)                          # expected label -> observed labels
    for t in turns:
        by_area[t["area"]]["turns"] += 1
        by_area[t["area"]]["passed"] += t["passed"]
        expected = t["expected_safety"]
        if isinstance(expected, str):
            safety[expected][t["observed"]["safety"]] += 1
    checks = Counter((c["check"], c["passed"]) for t in turns for c in t["checks"])
    recall = {label: {"cases": sum(seen.values()), "caught": seen[label]} for label, seen in sorted(safety.items())}
    return {"turns": len(turns), "passed": sum(t["passed"] for t in turns),
            "critical_failed": [t["id"] for t in turns if t["critical"] and not t["passed"]],
            "rubrics_not_judged": checks[("rubric", None)], "by_area": dict(sorted(by_area.items())), "safety_recall": recall}


def print_report(report: dict, verbose: bool = False, out=sys.stdout) -> None:
    s = report["summary"]
    print(f"Eval set {report['eval_set']} on target '{report['target']}': {s['passed']}/{s['turns']} turns passed, "
          f"{len(s['critical_failed'])} critical failures, {s['rubrics_not_judged']} rubrics not judged (no judge yet)", file=out)
    print("\nBy area:", file=out)
    for area, row in s["by_area"].items():
        print(f"  {area:<16} {row['passed']:>3}/{row['turns']:<3}", file=out)
    print("\nSafety labels (expected -> caught):", file=out)
    for label, row in s["safety_recall"].items():
        print(f"  {label:<28} {row['caught']:>3}/{row['cases']:<3}", file=out)
    failed = [t for t in report["turns"] if not t["passed"] and (verbose or t["critical"])]
    if failed:
        print("\nFailed turns" + ("" if verbose else " (critical only; -v for all)") + ":", file=out)
    for t in failed:
        print(f"  {'!' if t['critical'] else ' '} {t['id']} [{t['area']}] {t['message'][:70]!r}", file=out)
        for c in t["checks"]:
            if c["passed"] is False:
                print(f"      x {c['check']}: {c['detail']}", file=out)
        print(f"      answer: {t['observed']['answer'][:160]!r}", file=out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Score the chat on the eval set.")
    parser.add_argument("--set", default=str(EVAL_SET), help="eval set file")
    parser.add_argument("--target", default="rules", choices=["rules"], help="pipeline to score")
    parser.add_argument("--area", action="append", help="only this area (repeatable)")
    parser.add_argument("--id", action="append", help="only this conversation id (repeatable)")
    parser.add_argument("--out", help="write the full report as JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="list every failed turn, not only critical ones")
    args = parser.parse_args(argv)
    report = run(load(Path(args.set)), RulesTarget(), set(args.id or []), set(args.area or []))
    print_report(report, args.verbose)
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if report["summary"]["critical_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
