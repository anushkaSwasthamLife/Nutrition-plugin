"""Sheet for testing answers with real, non-expert readers (INDB plan, phase 5: the part a machine cannot do).

Run:  .venv\\Scripts\\python.exe -m dixit_answer.reader_test [--n 40] [--out FILE]

It asks the chat a spread of dish questions and writes a CSV: the question, the answer, the automatic readability result, and empty
columns for each reader to fill: did they understand it, which number they took from it, and what confused them. The chat is run with
the mock model, so the answers are exactly what the data produces.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import tempfile
from pathlib import Path

from .config import PROJECT_DIR, load_settings
from .llm import MockLLM
from .pipeline import Engine
from .readability import check_plain

QUESTIONS = ["how many calories in {d}", "{d} calories", "protein in {d}", "carbs in {d}", "fat in {d}", "what is the fiber in {d}"]
EXTRA = ["I am allergic to milk. calories in mango raita", "calories in cabbage rolls", "calories in chilli paneer", "calories in zzzz qqqq",
         "calories in dal", "how many calories in cabbage"]


def build_rows(engine: Engine, n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    codes = sorted(engine.dishes.names)
    questions = list(EXTRA)
    while len(questions) < n and codes:
        questions.append(rng.choice(QUESTIONS).format(d=engine.dishes.label(rng.choice(codes)).split(" (")[0].lower()))
    rows = []
    for i, question in enumerate(questions[:n], start=1):
        result = engine.answer(question)
        report = check_plain(result.answer, engine.words.get("jargon", []))
        rows.append({"id": i, "question": question, "answer": result.answer.replace("\n", " / "),
                     "reading_grade": "" if report.grade is None else f"{report.grade:.1f}", "machine_problems": "; ".join(report.problems),
                     "reader_understood (yes / mostly / no)": "", "number_the_reader_took_from_it": "", "what_was_confusing": ""})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--out", type=Path, default=PROJECT_DIR.parent / "Nutrition" / "indb_phase5" / "reader_test.csv")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    engine = Engine(load_settings(llm_provider="mock", llm_api_key="", var_dir=Path(tempfile.mkdtemp()), dish_lookup=True), llm=MockLLM())
    if engine.dishes is None:
        print("the dish lookup is off here (INDB files missing or production setting), so there is nothing to test")
        return 2
    rows = build_rows(engine, max(1, args.n))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    problems = sum(1 for r in rows if r["machine_problems"])
    print(f"wrote {len(rows)} answers to {args.out}; {problems} have a machine-found wording problem")
    return 0


if __name__ == "__main__":
    sys.exit(main())
