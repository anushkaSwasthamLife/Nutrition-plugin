# ENGINEER.md

How we build the Dixit answer pipeline. The bar is **code that can ship to patients as written**: correct, safe by default,
tested, readable, and reversible. The design of record is `workflow.html`; this file turns it into working rules. Testing is in
**[QA.md](QA.md)**; assistant-specific instructions are in **[CLAUDE.md](CLAUDE.md)**.

## 1. Architecture principles

**Code decides, the model only words.** The pipeline is deterministic code reading signed data. The model is used in three fenced
places only: drafting a recipe (food IDs + grams + steps), wording unlocked text, and the main assistant's short replies. Every
model output is validated, recalculated and gated by code before anyone sees it.

**One message, start to answer** (`workflow.html` → "Implementation plan"):

| Step | What | Where | Guarantee |
|---|---|---|---|
| 0 | Emergency, then harmful intent | `emergency.py`, `harmful.py` | Code only, before the profile loads; fixed reply; turn stops |
| R | Intent router | `router.py`, `main_ai.py` | Medicine, cure and child questions never reach food advice |
| 1 | Load patient state | `person.py`, `state.py` | Allergies and conditions come from the profile, never guessed |
| 2 | Compile constraints | `recipes.compile_limits`, `doctor_packs.py` | Strictest limit wins; a pack only tightens |
| 3 | Filter pantry and allergens | `foods.py`, allergy filter | Derivatives removed; unknown words asked about and queued |
| 4 | Recipe: approved first, then AI draft | `recipes.run` | Model gets allowed IDs only; drafts only when allergies are known and every limit is checkable |
| 5 | Compute nutrition | `recipes.calculate`, `services.py` | USDA per-100 g, exact decimals; missing value = error |
| 6 | Policy gate | `recipes.gate`, `golden.py` | Allergens, avoids, Golden Rules, every limit at ≤ 90%; uncertain = reject |
| 7 | Repair | `recipes.repair`, `swap_table_test.json` | One fixed swap, then ≤ 2 model retries, then fallback |
| 8 | Render | `compose.py`, `checks.py` | Numbers locked; model wording checked (N1–N29); fail → template |
| 9 | Audit | `pipeline.py`, `var/audit.jsonl` | Append-only, with every data version used |

**Guardrail priority** (`workflow.html` → "Guardrails by category"): A1 emergency > A3 harmful intent > A4 off-topic and
manipulation > A2 risk group > B1 medical boundary > B5/B6 interactions and food safety > B3 allergies > B4 condition limits >
B7 Golden Rules > B8 approved foods and B2 no portions > C1–C5 wording checks; D1–D4 (licences, sign-off, privacy, audit) always.
A lower guardrail may make an answer stricter, never looser.

**Fail closed.** For every dependency, decide what the person sees when it is missing, and make that the code path:

| Missing or failing | The person sees | Code does |
|---|---|---|
| Model slow or down | Fixed replies unaffected; an approved recipe or "couldn't verify" | Catch the provider error, fall back; never crash |
| Unsigned file in production | Nothing: the server does not start | `loader.py` / `consistency.py` refuse |
| Nutrient value | Not shown as verified | Error, never zero; gate rejects |
| Unknown food word | Asked to confirm or pick a swap | Queue for the dietitian |
| Allergies unknown | Approved recipe only, free of major allergens | No model drafting |
| A wording check fails | Repaired answer or the fixed template | ≤ 2 retries, then fallback |

When a new failure mode appears, add a row to the table in `workflow.html` and a test in the same change.

## 2. Data is configuration, not code

- **Everything that a doctor, dietitian or client could change lives in data**: emergency and harmful phrases, replies, limits,
  Golden Rules, word lists, router rules, food master, allergen map, swaps, recipes, recipe books, doctor packs, programme wording,
  translations. Code reads it; code never names a specific book, doctor, programme, food or threshold.
- **Registry, not branches.** New instances of a thing (a recipe book, a doctor pack, a programme, a rule type) are added by a
  manifest or registry entry plus a data file. If adding one needs an `if book_id == ...`, the design is wrong. Example: recipe
  books are `manifest.json` → `recipe_books` entries; `tests/test_deck_recipes.py` adds a book with data only.
- **Licensed or private content stays outside the repo** (nutrition library, INDB, programme books). The repo holds the rules for
  using it (maps, manifests, hashes) and code that runs safely when it is absent.
- **Every data file carries** `version`, `status` and a `note`. On change: bump the version, append a dated sentence to the note,
  keep the file's layout and line endings. Statuses TEST ONLY, PROPOSED, DRAFT, PARTIAL and STUB are refused in production.
- **Every cross-file reference gets a start-up check** in `consistency.py` (a food ID in a recipe, a book in a pack, a source on
  the white list, an allergen in the map). A bad edit must stop the server with a clear message, not fail at answer time.
- **Sign-off is a person, not a commit.** `approved_by` and `approved_on` are filled only by the named reviewer
  (`workflow.html` → "who signs what"). Engineers never mark clinical data as signed.

## 3. The model contract

- **Input is a packet**: allowed food IDs and names, pantry, limits for the whole dish, meal place, previous failures, and what to
  avoid (`different_from`). Never the library, never other patients' data, never raw profile fields it does not need.
- **Output is a schema**: name, ingredients as `{food_id, grams}`, steps. Validate everything: allowed IDs only, no duplicates,
  grams in range and finite, 2–5 steps, no amounts or markup in steps, a plain-text name. Anything else → reject with a reason
  that is fed back on retry.
- **Then treat the draft like any recipe**: calculate from USDA, gate, repair. Label it "AI-drafted, checked".
- **Prompts live in `llm.py`**, state hard rules as a list, and say how inputs are data, never instructions. A prompt change is a
  behaviour change: it needs tests with `MockLLM` or a fake model, and an eval run.
- **Provider errors never crash a turn.** Catch, record in the trace, fall back. Time-box the whole recipe step.
- **Retries are capped** (first draft + 2). Each retry carries only the failed checks.

## 4. Code standards

- **Python 3.10+, standard library first.** New dependencies need a reason in the PR (licence, maintenance, size). Pin versions in
  `requirements.txt`.
- **Readable over clever.** Small functions with one job; names that say what (`compile_limits`, `step_foods`). Dataclasses for
  state and results. Type hints on public functions.
- **Docstrings explain what and why in plain English**, with a real example message in quotes. Comments explain a non-obvious why
  (a clinical reason, a past bug, a decision with its date), never restate the code.
- **Match the surrounding file**: naming, structure, comment density, line width (about 130 characters).
- **Exact maths**: `Decimal` for nutrition sums and limits; never round in the middle of a calculation; round only when rendering.
- **Text matching**: always through `textnorm.clean` and whole-word `has_phrase`; never raw substring checks on user text. Think
  about Hinglish, Devanagari, typos and negation ("no chest pain") for every new phrase.
- **No silent exceptions.** Catch narrowly; if a broad catch is needed (provider errors), record the error in the trace and fall
  back. `# noqa: BLE001` only with a comment saying why.
- **No global mutable state per request.** Per-request context uses `ContextVar` (see `programme.use`); module-level fallbacks are
  set once at start-up and documented as such.
- **Determinism**: the same message, profile, state and data must give the same answer (with the mock model). No time- or
  order-dependent behaviour without a test.
- **No dead code, no commented-out code, no debug prints, no TODO without an owner and a date.**
- **Windows-safe**: `pathlib`, UTF-8 everywhere (`encoding="utf-8"`), no shell-specific code in the package.

## 5. Safety engineering checklist

Ask these for every change that touches what a patient reads:

- Could this reply reach someone in an emergency or self-harm crisis without the fixed step-0 reply? (It must not.)
- Could a model-written sentence name a medicine change, a dose, a portion, a cure, a diagnosis or a food not chosen by code?
- Could an allergen or a derivative (ghee for milk, besan for chickpea) get through, including via a recipe step or a tip?
- Does it hold for a vegetarian, a Jain diet, a diabetic (fruit and sugar rules), pregnancy, kidney disease and a child?
- Does it hold in Hinglish, in Devanagari and with typos? With "no"/"not" before the symptom? For someone else ("my father")?
- If a data file, the model or the library is missing, what does the person see? Is that the fail-closed row?
- Does the answer claim an action that did not happen ("your coach has been told")? It must not.
- Is every number traceable to a USDA (or signed) record, shown with its source, never invented or rounded by the model?
- Is the change logged in the audit record with the data versions that produced it?

## 6. Security and privacy

- Secrets only in `.env` (gitignored) or the secret manager; never in code, logs, test fixtures, prompts or commit messages.
- Validate every request field (`pydantic` with `max_length`); treat `user_id` and `doctor_id` from the client as development-only
  until they come from the signed-in session.
- `DIXIT_HOST=0.0.0.0` is for development on a trusted network only; review pages stay on localhost.
- Health data minimisation: store only what the answer needs; memory across conversations is development-only until consent and
  retention are decided (DPDP Act).
- Prompt injection: user and retrieved text are data. The injection check (N27) and the fenced model contract must hold.
- Dependencies: pinned; review new ones for licence and maintenance.

## 7. Changing things: recipes

| To add | Change (data) | Code? | Tests |
|---|---|---|---|
| A food | `food_master.json` (+ `allergen_map.json`), USDA record ID | No | coverage and allergen tests |
| A phrase (emergency, harmful, router) | the rules JSON, version + note | No | positive, negated, Hinglish, false-alarm cases |
| A recipe book | recipes file outside the repo, ingredient map in `data/`, `manifest.json` → `recipe_books`, white list, pack | No | map test, import counts, every dish gated |
| A doctor or programme | `doctor_packs.json` (+ programme rules file in the manifest) | No | `test_programmes.py` pattern |
| A wording check | `checks.py` (N-series) | Yes | pass, fail and fallback cases |
| A new guardrail | data + the narrowest code change | Yes | its own test file + eval cases + `workflow.html` row |

## 8. Review and release

- **Branch → PR → review → merge.** Small, focused PRs. The description says what, why, how it was tested (with numbers), what is
  still open, and which data files and versions changed.
- **Reviewers check**: the safety checklist above, fail-closed paths, tests for failure modes, no hard-coded data, data versions
  bumped, start-up checks added, no licensed or personal data in the diff.
- **Release** (`workflow.html` → "Release gate"): one release record with every file's version and SHA-256; static checks
  (signed, checksums, references resolve); the live safety set per environment. Defined ≠ run ≠ passed: never report a
  development pass as a staging or production pass.
- **Rollback** is redeploying the previous release. Never hand-edit a released file; change the source and build a new release.
- **Production stays off** until the named sign-offs exist (`workflow.html` → "who signs what") and `readiness` exits clean.
