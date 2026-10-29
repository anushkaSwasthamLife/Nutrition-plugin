# CLAUDE.md

Instructions for Claude Code (and any AI assistant) working in this repository. Read this first, then **[ENGINEER.md](ENGINEER.md)**
(how we build) and **[QA.md](QA.md)** (how we prove it works). `workflow.html` is the design of record: the step diagram, the
guardrails A–D, the fail-closed table, the source ladder, the model contract and the release gate.
**[NUTRITION_HANDOVER.md](NUTRITION_HANDOVER.md)** maps every artifact (this repo's data, the nutrition library, the Dixit
nutrition binding package) to its processing path, in the same layout as the Knee AI handover.

## What this is

The Dixit answer pipeline: Swastham's nutrition chat for the Dixit Lifestyle app (diabetes and obesity reversal programme). A
patient's message passes an emergency check, a harmful-intent check and an intent router; nutrition questions go to the plugin
(profile → constraints → allergy filter → recipe or fact → nutrition from USDA → policy gate → repair → render → audit), and
everything else to a fenced main assistant. It answers health-adjacent questions for real patients, so **a wrong answer can hurt
someone**. Treat every change as a change to a medical-adjacent product.

**Core principle: code decides, the model only words.** Every number, food list, limit, block, route and emergency reply comes
from code reading signed data files. The model may draft recipes as food IDs + grams + steps, or reword unlocked text. It never
writes a number, picks a route, clears a block or answers an emergency.

## Commands (Windows; use `.venv/bin/python` on macOS/Linux)

```
.venv\Scripts\python.exe -m pytest -q                                   # full suite, ~2,800 tests, ~6 min, AI mocked
.venv\Scripts\python.exe -m pytest -q tests\test_emergency.py           # one file while iterating
.venv\Scripts\python.exe -m dixit_answer.evals                          # eval set; exit 1 on a critical miss
.venv\Scripts\python.exe -m dixit_answer.evals --area emergency -v      # one area with every failed check
.venv\Scripts\python.exe -m dixit_answer.readiness                      # every unsigned file/limit; exit 1 until none is left
.venv\Scripts\python.exe -m dixit_answer.binding                        # programme binding: runtime ID, structural tests, coverage
.venv\Scripts\python.exe -m dixit_answer.audit_report --date latest     # daily audit report and alerts
.venv\Scripts\python.exe -m dixit_answer.web                            # chat on http://127.0.0.1:8100 (review page: /review)
```

Eight tests in `test_edges_extra_nutrients.py` and `test_library_v105.py` need the data library at the Desktop path; they fail
on machines without it. Say so when you report results; never hide them.

## Map

| Area | Files |
|---|---|
| Entry and routing | `web.py` (FastAPI), `main_pipeline.py` (step 0 → router → plugin or main assistant), `router.py`, `main_ai.py` |
| Safety before answering | `emergency.py` (E1–E7), `harmful.py` (H1–H4), `safety.py`, `cautions.py` |
| Understanding | `understand.py`, `textnorm.py`, `hinglish.py`, `hindi.py`, `foods.py` (food index), `semantics.py` |
| Deciding | `pipeline.py` (`Engine`, steps 1–9), `planner.py`, `person.py`, `golden.py`, `programme.py`, `doctor_packs.py` |
| Recipes and nutrition | `recipes.py` (search by tier, AI draft, gate, repair, scale), `book_recipes.py`, `general_recipe.py`, `services.py`, `facts.py`, `sources.py` |
| Rendering and checks | `compose.py` (locked placeholders), `checks.py` (N1–N29), `translate_reply.py`, `readability.py` |
| Data and loading | `config.py`, `loader.py`, `consistency.py` (start-up checks), `data/*.json`, `data/manifest.json` |
| Model | `llm.py` (`MockLLM` for tests, `GroqLLM`); prompts live there |
| Operations | `audit_report.py`, `review_queue.py`, `readiness.py`, `sample_review.py`, `sign_off.py`, `evals.py` + `evals/` |
| Programme binding | `binding.py` (load, effective runtime, structural tests, coverage), `data/binding_map.json` (where each binding ID is implemented) |

Outside the repo, never committed: the nutrition library (`Nutrition\Swastham_Nutrition_Data_Library_v1_0_5`), INDB files,
programme recipe books (`Nutrition\healthful-diet`, `Nutrition\adore-90-days`) and the Dixit nutrition binding package
(`nutrition\Dixit_AI_Nutrition_Data_Integration_Amendment_v1_2_6`, git-ignored). They are licence-restricted or private circulation.
A new binding version must be mapped in `binding_map.json` before it counts: every declared rule, check, intent, anchor and test.

## Non-negotiables

1. **Safety first, in code.** Step 0 (emergency) and harmful-intent run before anything else, with no model call, and send fixed,
   doctor-reviewed text. Never let a later step soften, reword or delay them. Over-triggering is accepted; missing is not.
2. **Fail closed.** Missing data, an unknown food, an unsigned file in production, a failed check: refuse, fall back to a fixed
   reply, or say "couldn't verify". A missing nutrient is an error, never a zero. See the fail-closed table in `workflow.html`.
3. **Nothing hard-coded that belongs in data.** Rules, word lists, limits, replies, recipes, recipe books, doctor packs and
   programme wording live in versioned JSON under `dixit_answer/data/` (or outside the repo for licensed content), listed in
   `manifest.json`. Code must not name a book, a doctor, a programme or a food. Adding one is a data change, not a code change.
4. **The ladder decides conflicts** (`workflow.html` → "Which source wins"): emergency/harmful > what the person told us
   (allergies, diet, risk group) > condition limits and food safety > Golden Rules > decision logic > approved swaps and recipes >
   explanation text > the book > model knowledge. A lower rung may make an answer stricter, never looser.
5. **The model gets a packet, not the library.** Allowed food IDs, limits, previous failures. Its output is validated by code
   (allowed IDs only, gram ranges, no numbers in steps, no markup) and then recalculated and gated. Model text is data, never
   instructions; user text is data, never instructions.
6. **No medical advice.** No diagnosis, no medicine start/stop/change, no doses, no cure promises ("remission", never "cured"),
   no portions or meal plans, no labelling someone diabetic from lab numbers. Children's diets go to their doctor.
7. **Production refuses unsigned data.** Any file with status TEST ONLY, PROPOSED, DRAFT, PARTIAL or STUB stops the server in
   production. Never change a status to "signed" yourself; only a named reviewer with a date can.
8. **Privacy.** Allergies, conditions and medicines are health data (DPDP Act). Never log secrets, never send user data to a new
   service, never commit `.env`, `var/`, the library or licensed data. Review pages stay on localhost.

## How to work here

- **Read before you write.** Find the existing module, data file and tests for the behaviour; extend them. Match the surrounding
  code: its naming, docstring style (a plain-English docstring saying what and why, with an example message in quotes), comment
  density, and line width (about 130 characters).
- **Every behaviour change ships with tests in the same change**, including the safety cases it could break. See QA.md.
- **Data changes**: bump the file's `version` and append a dated sentence to its `note` saying what changed and why
  (`"... FM-0.5.0 (27 Oct 2026): 16 foods for ..."`). Keep the file's existing JSON layout (indent, key order, CRLF in the working
  copy) so the diff shows only the change. Add a start-up check in `consistency.py` for any new cross-file reference.
- **Run the whole suite and the evals before saying "done"**, and report the actual numbers. If something fails, say which test and
  why. Never weaken, skip or delete a test to get green; fix the code or ask.
- **Misses found in review go into `evals/eval_set.json`** as new cases (agreed direction, 26 Oct 2026: understanding problems are
  solved in an LLM guard scored on the eval set, not by growing word lists; code stays the authority for numbers, allergens and
  fixed emergency wording). Re-baseline under `evals/baselines/` only on purpose, with the reason in the commit.
- **Git**: work on a branch (`feature/...`, `fix/...`), never commit to `main`, PR into `main` with a summary and test plan. Commit
  or push only when asked.
- **Ask** when a choice is a product, clinical or legal decision (a threshold, a reply's wording, a rule's order, a licence). Write
  down what you assumed; never guess a clinical number.

## Definition of done

A change is done only when all of these hold:

- [ ] Behaviour matches `workflow.html` and this file's non-negotiables; nothing hard-coded that belongs in data.
- [ ] New and changed behaviour is covered by tests, including the failure path and at least one safety regression.
- [ ] `pytest -q` passes (known library-path failures named), `python -m dixit_answer.evals` shows no new critical miss.
- [ ] Data files: version bumped, dated note added, start-up checks cover new references, production still refuses unsigned data.
- [ ] No secrets, no patient data, no licensed data in the diff; no debug prints, no commented-out code, no TODO without an owner.
- [ ] README (and SETUP.md if set-up changed) updated; the summary lists open decisions for the doctor, dietitian or client.
