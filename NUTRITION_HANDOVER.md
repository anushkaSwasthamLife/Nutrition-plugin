# Nutrition AI implementation handover

What each nutrition artifact is, how the pipeline uses it, and how it plugs into Swastham.ai. Same layout as the Knee AI handover
guide (v1.1, 10 Aug 2026), with nutrition content. Read this first, then [CLAUDE.md](CLAUDE.md), [ENGINEER.md](ENGINEER.md) and
[QA.md](QA.md). `workflow.html` stays the design of record.

| Field | Value |
|---|---|
| Guide version | 0.1 (27 Oct 2026) |
| Programme | Dixit Lifestyle (diabetes and obesity reversal), nutrition plugin |
| Programme binding | Dixit AI Nutrition Data Integration Amendment v1.2.6 (`DIXIT-NUTRITION-1.2.6`) |
| Nutrition library | Swastham Nutrition Data Library: **v1.0.5 in use**, v1.0.3 pinned by the binding (open decision 1) |
| Effective runtime on this machine | `DIXIT-RT-none+OVRnone+NUT1.2.6+SNDL1.0.5`: **not a validated combination** |
| Binding `production_enabled` | FALSE |
| Pipeline release | `DIXIT-ANSWER-0.3.0-dev` (manifest `runtime_id`) |
| Live model integration | Groq `openai/gpt-oss-120b`, development only |

Check the current state at any time:

```
.venv\Scripts\python.exe -m dixit_answer.binding        # binding, effective runtime, structural tests, coverage
.venv\Scripts\python.exe -m dixit_answer.readiness      # everything production would refuse
.venv\Scripts\python.exe -m dixit_answer.evals          # eval set, incl. the binding's chat tests (area "binding")
```

## 1. The one-page mental model

- **Safety is deterministic and runs first.** Emergency (E1–E7) and harmful intent (H1–H4) are checked in code, with fixed
  doctor-reviewed text and no model call, before routing, retrieval or generation. Over-triggering is accepted; missing is not.
- **Rules decide what is allowed.** Golden Rules, condition limits, allergens, diet, doctor packs and the binding's nutrition
  rules (NUT-*, UNIV-PERSONAL-001, PATCH-MOD-001) are deterministic data read by code. Neither the model nor retrieval chooses a
  route, clears a block or picks a food.
- **The nutrition library supplies facts, not permission.** A record says what a food contains; whether the person may eat it is
  decided by the Golden Rules and the person's profile (binding principle 4).
- **The manifest decides what exists.** Data files, recipe books, environments, the library pin and the programme binding are
  listed in `dixit_answer/data/manifest.json`. Code never names a book, doctor, programme or food.
- **The model only words.** It drafts recipes as food IDs + grams + steps (validated, recalculated, gated) and rewords unlocked
  text. Numbers, routes, emergencies and blocks are locked.
- **Tests prove it, per layer.** Defined is not run, and run is not passed. The binding's 15 structural tests report "not run"
  when they cannot run; its chat tests are eval cases whose rubrics stay "not judged" until an LLM judge exists.
- **Nobody monitors the chat clinically.** No answer may say a coach or doctor was told, will call back or reviewed anything.

## 2. Artifact processing matrix

Every artifact belongs to one processing path. Do not embed the folder as one corpus.

| Artifact | Bucket | Required action | Do not |
|---|---|---|---|
| `emergency_rules.json`, `harmful_intent_rules.json` | DETERMINISTIC RULES, step 0 | Run in code before anything else; fixed replies | Let a model or retrieval decide or reword a safety reply |
| `golden_rules.json`, `condition_limits_test.json`, `decision_logic_draft.json`, `rules_registry.json` | DETERMINISTIC RULES | Constraints, protocol positions, ≤ 90% limit gate | Index as answer content; let a lower rung loosen an answer |
| Binding `03_rules/*` (9 NUT rules, UNIV-PERSONAL-001, PATCH-MOD-001), `02_registration/rule_priority_registry.json` | DETERMINISTIC RULES (programme binding) | Map each rule to its implementation (`binding_map.json`); priorities unique across the merged runtime | Re-resolve precedence in prose or code branches |
| Binding `03_rules/conflict_supersession_registry_addendum.json` | DETERMINISTIC PRECEDENCE | The one place conflicts are resolved (CONF-NUT-001..007) | Resolve the same conflict differently anywhere else |
| `allergen_map.json`, `black_lists.json`, `white_lists.json`, `food_cautions_test.json` | DETERMINISTIC FILTERS | Applied before any recipe, fact or draft | Treat as advisory |
| Swastham Nutrition Data Library (`nutrition/…v1_0_3`, `…v1_0_5` outside the repo) | STRUCTURED FACT SERVICE | Hash-pinned load; drop quarantined and ineligible records; apply `usage_constraint`, `energy_basis`, approximation disclosure | Put in a vector index as free text; infer gram weights; average conflicting records |
| `indian_composition_estimation_support.jsonl` | INTERNAL LOOKUP ONLY | Ingredient reconstruction fallback | Show to users |
| Source workbooks (`*_CORRECTED.xlsx`) | SOURCE DATA | Traceability and library regeneration | Read at runtime; register their condition flags or Condition Guidance labels |
| `food_master.json`, `book_ingredient_map.json`, `deck_ingredient_map.json`, `cooking_factors.json`, `book_measures_draft.json` | STRUCTURED CONFIGURATION | Food identity, aliases, record choice, unit weights | Guess a food or a weight the file does not hold |
| Recipe books (Healthful Diet, 90 Days Challenge; outside the repo) + `recipes_test.json` | CONTROLLED RETRIEVAL (exact identity) | Offered by tier (doctor > programme book > dietitian), recalculated and gated, credited | Fuzzy-substitute a different dish without saying so |
| `doctor_packs.json`, programme rules | STRUCTURED CONFIGURATION (programme) | Which books, limits and wording a doctor's patients get; a pack only tightens | Hard-code a programme or doctor |
| `manifest.json` | CONFIGURATION AUTHORITY | Files, pins, environments, recipe books, programme binding | Infer what exists from file names |
| Binding `04_conversation/*` (18 intents, orchestration patch, response patterns) | BEHAVIOUR + ORCHESTRATION | Classifier emits the declared sub-intents; steps sit at the declared anchors | Accept a caller- or test-injected intent as authoritative |
| `router_rules.json`, prompts in `llm.py`, `checks.py` (N1–N29) | BEHAVIOUR POLICY + VALIDATOR | Main-assistant scope, reviewed replies, post-generation checks | Index as answer content; let a prompt widen scope |
| Binding `03_rules/nutrition_answer_validator_addendum.json` (14 NUT checks) | VALIDATOR | Run after the merged final-answer validator (see coverage) | Count a check as implemented because a similar N-check exists |
| `support_content.json` (wellbeing) | APPROVED KNOWLEDGE (small) | Quoted with source and licence | Extend with unsourced text |
| `evals/eval_set.json`, binding `05_tests/*`, `tests/` | QA / RELEASE GATE | Automated tests; results recorded per environment | Put fixtures in retrieval; report "defined" as "passed" |
| `binding_map.json` | TRACEABILITY | Where each binding ID is implemented and how completely | Treat a status as a sign-off |
| `workflow.html`, this guide, `CLAUDE.md`, `ENGINEER.md`, `QA.md` | GOVERNANCE / ENGINEERING CONTRACT | Design of record and working rules | Serve to users |

**Runtime flow:** message → step 0 (emergency, harmful intent) → router → profile and state → intent and sub-intent → rules and
pack → filters (allergens, diet, eligibility, environment sources) → fact lookup or recipe tier → (model draft, validated) →
recalculate → gate → repair → compose with locked numbers → validate (N-checks; NUT checks to come) → audit.

## 3. Six truth layers

| Truth layer | Authoritative artifacts | Use | Do not |
|---|---|---|---|
| 1. Deterministic safety and rules | emergency, harmful-intent, Golden Rules, limits, allergens, binding rules and conflict registry | Code before retrieval and generation | Let semantic search or the model decide safety, permission or a route |
| 2. Knowledge | Nutrition library (facts), recipe books and approved recipes, `support_content.json`; **missing: an approved nutrition FAQ / condition-knowledge base** | Facts and approved explanations after filtering | Let content override a rule |
| 3. Configuration | `manifest.json`, `doctor_packs.json`, programme rules, binding manifest | What exists, which programme, which book, which environment | Hard-code a book, pack, source or count |
| 4. AI behaviour | Router rules and replies, prompts (`llm.py`), N-checks, binding intents, response patterns and NUT checks | System constraints and post-generation validation | Index as answer content |
| 5. UX and state | `state.py`, badges, chips, check-in line; **missing: a UX contract for the swastham.ai app** | The app shows the backend's decision | Let the app infer clinical state from text |
| 6. Verification and release | `tests/`, `evals/`, binding tests, `readiness`, release record (PR #3) | Release gate, reported per environment | Call a defined test passed |

## 4. Read in this order

1. This guide. 2. `workflow.html` (step diagram, guardrails A–D, fail-closed table, source ladder, model contract, release gate).
3. Binding `00_READ_ME_FIRST.md`, then `01_manifest/amendment_manifest.json` and `02_registration/*`. 4. Binding `03_rules/*`.
5. Library `00_READ_ME_FIRST.md` and `04_runtime/*`. 6. `CLAUDE.md`, `ENGINEER.md`. 7. `dixit_answer/data/manifest.json` and the
data files it lists. 8. `QA.md`, the eval set and the binding's `05_tests/*`.

## 5. Machine state: one field, one meaning

| State | Where | Why separate |
|---|---|---|
| Profile: allergies, diet, conditions, medicines, pregnancy | App profile → `person.py` | Decides what is removed; never guessed from a message |
| Diabetes status, prior history, consultant validation | Profile, or stated in chat (`status.py`) | Fruit and sugar rules depend on it; never computed from lab numbers |
| Risk flags (diabetes or BP medicine, pregnancy, kidney disease, under 18) | Safety gate → chat state | Adds cautions only; someone else's age or condition never sets the person's own (fixed 27 Oct 2026) |
| Emergency state (last category, turns since) | Chat state | One check-in line within 3 turns |
| Dish-search memory (wishes, leave-outs) | Chat state, shown as chips | The person can see and undo it |
| Data licence per source | Manifest environments | Production refuses development-only data |
| Versions and effective runtime | Every audit record (`versions`) | Replay any answer with the exact files |
| Operational actions completed | Not recorded; none exist | An answer may claim an action only after a real one succeeds |
| Monitoring status | Always "not monitored" | No answer implies clinical review |

## 6. Rules the team must preserve exactly

These live in data; the files are the authority, this list is a pointer.

- Step 0 runs first, on the whole message, in every language we read (English, Hinglish, Devanagari), with negation per clause.
- Two meals a day, each in the order nuts → salad → sprouts → home meal, finished within 55 minutes; nothing between meals
  (`golden_rules.json`). Generic frequent-meal advice is shared only as attributed comparison, never as the programme's position
  (CONF-NUT-003).
- Fruit by diabetes status; after diabetes, the restriction continues until a consultant confirms (CONF-NUT-004, NUT-STATUS-001).
  Neutral nutrient facts never require the status (CONF-NUT-007).
- No portions, meal plans, doses, medicine changes, diagnoses or cure promises (UNIV-PERSONAL-001).
- Insulin demand, GI, GL and FII are not approved values: declined, never estimated (NUT-FIELD-001; fixed 27 Oct 2026).
- Quarantined or ineligible library records never influence an answer (NUT-DATA-ELIGIBILITY-001, NUT-QUAR-001).
- Every number comes from a record or a recorded calculation; a missing value is an error, never a zero (NUT-SRC-001).
- Every recipe limit is checked at ≤ 90%; allergens and derivatives are removed before anything else; a pack only tightens.
- If a rule, a file or a value is missing or uncertain, fail closed (the table in `workflow.html`).

## 7. What belongs in retrieval, and what does not

| Content | In a search index? | Handling |
|---|---|---|
| Approved nutrition FAQ and condition knowledge (to be written) | YES | One record per approved answer, with ID, source, conditions, `blocked_if`, diet and language tags; filter before ranking |
| Recipes (approved, programme books) | YES, exact identity | Matched by name or ingredients after allergy, diet and pack filters; a different dish is named as different |
| Persona variants (diabetic, weight loss, Jain, elderly, caregiver, Hinglish) | YES, query expansion only | Resolve to the canonical answer; never change a route |
| Nutrition library records | NO (structured lookup) | Exact and alias matching with eligibility and usage constraints |
| Rules, Golden Rules, limits, conflict registry | NO as decision source | Deterministic code; explanation text may be retrieved after the decision |
| Manifest, doctor packs, binding | NO | Configuration lookup |
| Behaviour policy, prompts, checks | NO | System constraints and validators |
| Tests and eval cases | NO | QA only |
| Model knowledge | NO authority | Wording only; never a new fact, food, warning sign or amount |

## 8. Release workflow

1. Edit the governed source (the data file, or the library/binding package upstream), never a derived copy.
2. Bump the version and add a dated note; add start-up checks for new references (`consistency.py`).
3. Build one release record with every file's version and SHA-256 (PR #3's release build).
4. Static checks: start-up, `readiness`, `python -m dixit_answer.binding` (structural tests, coverage).
5. Development run: `pytest -q`, `python -m dixit_answer.evals` (no new critical miss beyond `evals/baselines/`).
6. Staging run on the production model; record staging results separately.
7. Production only when readiness is clean, the binding combination is validated, the binding's own `production_enabled` is
   true, and the named sign-offs exist. Production is a decision, never the side effect of importing data.

## 9. Audit contract

Recorded now on every answer: conversation and turn, safety label, route and handled-by, plan type and blocks, rules applied,
recipe trace, model method and validation result, sources, every data version, the binding state and the effective runtime ID.
Still to add (binding `ANCHOR_LOGGING` and orchestration patch): rule IDs by binding ID, anchor trace, `nutrition_subintent`,
`nutrition_record_ids`, `source_dataset`, `match_type`, `serving_basis`, `calculation_trace`, `golden_rule_ids`,
`approximation_disclosure`.

## 10. What not to rebuild

- No LLM-only triage in place of step 0. No model-chosen route, food, amount or permission.
- No new clinical thresholds, warning signs, fruit rules or meal rules in code or prompts.
- No re-deriving the library from the workbooks at runtime; no patching library files (a change is a new library version).
- No duplicate copies of the binding's rules in prose; the coverage map points to where each is implemented.
- No "passed" for a test that did not run at that layer; no production because static checks passed.

## 11. Current status

| Area | Status | Meaning |
|---|---|---|
| Step 0 safety | Built, rules TEST ONLY / PROPOSED | Doctor sign-off pending |
| Programme binding 1.2.6 | Loaded, hash-verified, **not validated** | Baseline 1.1 and overlay 1.1.1 missing; library 1.0.5 ≠ pinned 1.0.3 |
| Structural tests (15) | 11 passed, 2 failed, 2 not run on library v1.0.5 | STR-008 and STR-013 fail on v1.0.5 and pass on v1.0.3; STR-006 and STR-011 need the baseline |
| Binding coverage | rules 3 implemented / 7 partial / 1 missing; checks 5 / 6 / 3; intents 12 / 3 / 3 | See `python -m dixit_answer.binding` |
| Binding chat tests (25 as evals) | 20 of 25 pass their machine checks on the rules target; rubrics not judged | Gaps: per-piece servings, meal estimation, serving conversion, condition knowledge |
| Knowledge base (FAQ, condition knowledge) | Not started | Needs approved content and an owner |
| Behaviour policy as data | Not started | Prompts still in `llm.py`; live run showed scope leaks (LP-01..03) |
| UX contract | Not started | |
| Release build | In PR #3 | |
| Production | OFF | Readiness lists every open point |

## 12. Acceptance checklist

- [ ] Step 0 and harmful intent run before routing and retrieval, and reproduce the fixed replies word for word.
- [ ] No answer gives permission, a portion, a plan, a dose or a medicine change; condition knowledge is education only.
- [ ] Quarantined, ineligible or excluded values (insulin demand, GI/GL/FII) never reach an answer.
- [ ] Neutral facts never ask for diabetes status; status-dependent answers use the profile or ask one question.
- [ ] Every number traces to a record and a version; every answer's audit carries the effective runtime ID.
- [ ] A different dish is never offered as the one asked for.
- [ ] No claim of monitoring or of an action that did not happen.
- [ ] The binding loads hash-verified, its combination is validated, and its structural tests pass with none "not run".
- [ ] Every binding ID is mapped, and every "partial" or "missing" has an owner and a plan.
- [ ] The eval set (including the binding's chat tests) runs on the production stack, with results recorded per environment.
- [ ] Production is switched on by a named release decision.

## 13. Ownership

| Owner | Responsibility |
|---|---|
| Doctor (Dr. Dixit's team) | Emergency and harmful replies, Golden Rules, fruit and status rules, child and pregnancy boundaries |
| Dietitian | Food master, limits, swaps, recipes, books' ingredient maps, FAQ and condition knowledge |
| Knowledge operations | Library versions, binding package, knowledge-base records and provenance |
| AI engineering (this repo) | Pipeline, filters, retrieval, model contract, validators, binding coverage, audit |
| Platform (swastham.ai) | Auth, profile, state store, the plugin API, UX states |
| QA | Tests, eval set, live runs per environment, release report |
| Release owner | Release record, rollback, the production decision |

## 14. Open decisions

1. **Library version.** The binding pins v1.0.3; the pipeline runs v1.0.5, which adds gram weights and 103 energy values
   "calibrated to USDA fried analogues; pending dietitian sign-off". Either the binding is re-issued for v1.0.5 or the pipeline
   moves back to v1.0.3.
2. **The Dixit baseline v1.1 and overlay v1.1.1.** Needed to validate priorities (STR-006, STR-011, NUT-T025), scenarios SCN-089/090
   and the FAQ collections.
3. **The swastham.ai plugin contract** used by the Knee plugin (API, programme ID, who holds member state).
4. **Knowledge base content:** who writes and signs the nutrition FAQ and condition-knowledge records, and whether the private
   programme books may be indexed as text or only used through derived rules.
5. **Profile condition as status:** whether "type 2 diabetes" in the profile's conditions counts as the person's stated status
   (eval LP-07).
