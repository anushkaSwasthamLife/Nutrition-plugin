# Dixit answer pipeline

Swastham's own chat answer system for the Dixit Lifestyle app, built from
`Documents\Dixit_Chat_Answer_Architecture.html` (design) and `Documents\Dixit_Chat_Answer_Analysis.html` (problems P1–P14, D1–D3).
Standalone Python package until the Dixit app code is available. **Development build: core pipeline, allergy filter, hybrid recipes, Golden Rules engine, emergency check, INDB dish answers (calories and nutrients for dishes like poha), white and black lists, plain-language wording, with a large edge-test suite.**

## Run

Full set-up on another machine (data folder, settings, checks, troubleshooting): **[docs/SETUP.md](docs/SETUP.md)**. In short:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe -m dixit_answer.web       # chat page on http://127.0.0.1:8100
.venv\Scripts\python.exe -m pytest -q              # about 2,100 tests, 6 to 15 minutes (no AI calls; the AI is mocked)
```

**The repo does not hold the nutrition data.** The chat will not start without the library folder
`Nutrition\Swastham_Nutrition_Data_Library_v1_0_5`, kept next to this project folder (or set `DIXIT_LIBRARY_DIR`). The INDB files in
`Nutrition\indb_source` and `Nutrition\releases` are optional and switch on the dish answers ("calories in poha"). The Indian records
are NIN-derived with the licence unconfirmed, so share that folder privately and never commit it.

Port 8100 so it never clashes with the Swastham website servers (8000, 5173). Python 3.10 or newer (tested on 3.13).

## AI wording

Same provider as Swastham Report analysis: Groq, `openai/gpt-oss-120b`. `.env` sets `LLM_ENV_FILE` to the Report analysis
`.env`, so the key is read from there and not copied. With no key, the pipeline uses the template wording (mock) and still works.

The AI only rewords a draft with `{{placeholders}}`. Every number, food list, approved label, protocol line, caution and
question is locked and filled in by code afterwards. A draft that fails the checks is retried once; then the template answer
is sent.

## How one message is answered (`dixit_answer/`)

| Stage | Module | What it does |
|---|---|---|
| 1 Receive | `pipeline.py` | Message + conversation ID; message used as data only |
| 2 Remember | `state.py` | Chat state per conversation (SQLite in `var/`) |
| 3 Safety gate | `safety.py`, `emergency.py` | Step 0 emergency check (E1–E7, `emergency_rules.json`, no AI); risk words (insulin, pregnancy, kidney…) with negation |
| 4 Understand | `understand.py`, `foods.py`, `textnorm.py` | Spelling variants, whole-word food matching, replies to our last question, 17 question types |
| 5 Plan | `planner.py` | Rules R1–R12 → answer plan (blocks, protocol level, follow-up) |
| 6 Fetch | `services.py`, `facts.py`, `recipes.py`, `person.py` | Golden Rules stub, approved food list, cooking tips, nutrition facts, **allergy / avoid / diet filter**, **hybrid recipe flow R1–R9** |
| 7 Compose | `compose.py`, `llm.py` | Template with locked items; optional AI rewording |
| 8 Check | `checks.py` | N1/N15, N4, N6, N9, N16, N17c, N18–N24, N25, N26, N27, placeholders, length |
| 9 Deliver + log | `pipeline.py` | Append-only `var/audit.jsonl` (versions, plan, records, checks, final text) |
| 10 Learn | `web.py` | Feedback buttons → audit log |
| Data | `loader.py`, `data/` | Hash-pinned library, environment switches, quality gates D1–D3 |

## TEST ONLY data (`dixit_answer/data/`)

`food_master.json`, `approved_list_test.json`, `cooking_tips_test.json`, `word_lists.json` are drafts; `golden_rules_stub.json`
only repeats what the live Dixit app said on 13 Oct 2026. Production refuses all of them until a dietitian / Dixit sign them.
The nutrition library is read in place from the `Nutrition` folder beside the project (`Swastham_Nutrition_Data_Library_v1_0_5`) and
checked against the hashes in `data/manifest.json`. Production loads USDA only.

## Recipes (phase 5)

Approved recipes first, AI-drafted + gated second, fallback last (`recipes.py`):

R1 map pantry words (unknown words are left out, queued for the dietitian, never sent to the AI; a close match is confirmed first)
→ R2 allergy / avoid / diet filter (derivatives such as ghee and paneer for milk) → R3 limits (strictest first)
→ R4 approved recipe match → R5 AI draft (only food IDs + grams + steps) → R6 calculate the whole dish from USDA values
(exact decimals, missing value = error) → R7 gate (allergens, Golden Rules, 90% of each limit) → R8 repair one
ingredient (never scale the dish), then AI retry (max 2) → R9 fallback (partly matching approved recipe, or tips).

Nothing unchecked is shown. Limits are never shown to the person. AI drafting is switched off in production in `manifest.json`
until a dietitian has reviewed a sample and the limits are signed, and it is also off whenever allergies are unknown or a
limit can't be verified (sodium and sugar are not in the library yet).

## TEST ONLY data added in phase 5

`recipes_test.json` (8 recipes), `allergen_map.json`, `condition_limits_test.json` (**placeholder numbers**),
`swap_table_test.json`, `cooking_factors.json`, and food master v0.2 (ingredient records, unit weights, pantry items).
Paneer uses USDA queso fresco as a PROXY record until a dietitian confirms a better one.

## Golden Rules (phase 6)

`golden_rules.json` (GR-PARTIAL-0.2) is **PARTIAL and unsigned**: built only from the live Dixit app's answers and the amendment tests.
Anything not documented is answered "unknown, ask your Dixit coach", never guessed. Production refuses this file.
Fruit position depends on diabetes status (asked once, only when a rule depends on it). Protocol amounts drop options the person
can't have (allergy, diet, avoid list) and say "ask your Dixit coach" when none is left.

## Edge testing

`tests/test_edges_*.py`, `test_edge_regressions.py` and `test_consistency_mutations.py`: hostile and odd input, unicode and very long text,
corrupt or old chat state, bad profiles, every allergen × diet combination, tampered data files, HTTP misuse, a 1500-message seeded fuzz and
40 multi-turn random walks. The start-up check (`consistency.py`) cross-checks every data file. Run all: `.venv\Scripts\python.exe -m pytest -q`.

## Not built yet

- Real Golden Rules (base package v1.1 + v1.1.1): the file above is partial.
- Phase 7: connecting to the Dixit app. Phase 8: dietitian / doctor / legal review.
- Fixes D1–D3 in the Indian library itself (the loader's quality gates only block the bad records).

## Documents

- `Desktop\Nutrition\workflow.html`: the recipe-guardrail diagram, used for reviewers.
- `Documents\Dixit_Chat_Answer_Architecture.html`: the full chat design (the source for this package).

## Emergency check (step 0, 16 Oct 2026)

`emergency.py` + `data/emergency_rules.json` (EM-0.1.0, **PROPOSED**: a doctor must sign it, and production refuses it until then).
Seven categories, highest priority first: E5 self-harm (Tele-MANAS 14416), E3 breathing or severe allergy, E2 heart or stroke,
E6 collapse, fits, bleeding or poison, E1 low sugar, E7 pregnancy warning signs, E4 very high sugar. They are caught by English,
Hinglish and Hindi phrases, by two-symptom combinations, and by sugar readings below 70 or at 300 mg/dL and above (placeholders;
mmol is converted, and food amounts such as "50 g sugar" are ignored). A match sends that category's fixed reply and stops the
turn, with no AI call and no question; check N28 confirms the text. A negated symptom ("no chest pain") adds one 112 line. The
next normal answer within 3 turns starts with one check-in line. The audit log records the matches and the rules version.
Tests: `tests/test_emergency.py`.

## Audit report and alerts (item C, 16 Oct 2026)

`audit_report.py` reads `var/audit.jsonl` and prints one day's counts (turns, gate rejections, repairs, AI retries, fallbacks,
emergency replies, unknown words, feedback), compared with the average of the earlier days that have data. Six alerts, set in
`data/alert_rules.json` (AR-0.1.0, **PROPOSED**: Swastham confirms the limits after a few weeks of real traffic):

```
.venv\Scripts\python.exe -m dixit_answer.audit_report --date latest          (or today, or 2026-10-16)
.venv\Scripts\python.exe -m dixit_answer.audit_report --json --fail-on-alert   (exit code 2 on a critical or warning alert)
```

It only reads the log; nothing it finds changes an answer. Tests: `tests/test_audit_report.py`.

## Unknown-item queue and dietitian review (item D, 16 Oct 2026)

Every food word we did not recognise, and every dish asked for that has no approved recipe, is written to the audit log as an
`unknown_item` record. `review_queue.py` reads them into `var/review_queue.sqlite3` (older log lines too) and the dietitian works
through them at **http://127.0.0.1:8100/review** while `python -m dixit_answer.web` runs:

- *another name for a food we have* adds an alias to that food
- *a new food* needs a name, a category, the allergens (confirmed on purpose) and a USDA record (Indian records are refused)
- *reject* needs a reason; for dishes, *add to the recipe list* keeps a wish-list for recipe writing

Every decision stores who made it. Nothing changes until **Apply**. Apply tries the decisions one at a time against the engine's
start-up consistency check: one that would break it (a name another food already uses, an allergen that left the map, a record that
no longer exists) is skipped and its reason is kept on the item, and the others still go in. It then writes `food_master.json`
(version bumped, a `review_log` entry per change) and, for allergens, `allergen_map.json`, each through a temporary file, and puts
the old text back if either write fails. Two Applies at once cannot apply the same decision twice. The chat uses the new list from
the next message. Development tool only:
production keeps the queue in the app database. Tests: `tests/test_review_queue.py`, `tests/test_review_web.py`.

## Extra nutrients (item E, 16 Oct 2026)

Library **v1.0.4** added six nutrients per 100 g to every USDA record: `sodium_mg`, `sugars_g`, `saturated_fat_g`, `cholesterol_mg`,
`potassium_mg`, `phosphorus_mg`. They come from the official USDA files via the USDA workbook (Fix 20, Change Log section 20), and
every other value is identical to v1.0.3. `null` means USDA publishes no value: the recipe maths treats it as missing data
("no nutrition data for X"), never as zero. Added sugars are in none of the three USDA downloads, so `added_sugar_g` stays
unverifiable and the reviewer must choose another route (limit `sugars_g`, or find another source).

The gate now adds up the six nutrients for the whole dish and checks any limit in `condition_limits_test.json` that names them
(`verifiable_nutrients`). They are never shown in answers. At start-up, every nutrient marked checkable must be one the maths adds
up and one the library carries. The library is built by `Desktop\Nutrition\usda_checks\build_library_v1_0_5.py`.
Library **v1.0.5** also carries workbook fixes 3 to 19 and the Indian corrections: USDA energy and fibre corrections, 461
`approved_flagged` records with a usage constraint, 6 new USDA records (13,560), gram weights for all 894 Indian recipes, and 103
fried dishes calibrated to USDA analogues (proposed, pending dietitian sign-off). USDA fibre and net carbohydrate are `null`
where USDA does not measure fibre.
Tests: `tests/test_extra_nutrients.py`.

## Emergency check: how a message is read (EM-0.1.3)

- **Clauses.** A message is split at `. , ; : ! ?`, the Hindi full stop and at "but" or "lekin". A negation counts only inside the clause
  of the symptom, and only when it sits right in front of it ("no chest pain", "without fainting or chest pain"). "There is no point,
  I want to die", "no, I fainted" and "without help I can't breathe" are emergencies. Self-harm (E5) is never set aside.
- **Only talked about.** A definition ("what is anaphylaxis"), something long past ("a heart attack last year") or a diet question
  ("what to eat after a stroke") gets a normal answer ending with "call 112 if that changes". "Now", "still", "again", "help" and
  similar words in the same clause cancel that, so "what is this chest pain" is still an emergency.
- **Readings.** `sugar 52`, `glucose 3.1 mmol`, and in Hindi script `शुगर ५२`. Food amounts (`50 g sugar`, `sugar 45%`) are ignored.
- **Known limits (listed for the doctor):** misspelled words are not caught; a second reading without the word "sugar" in front of it
  is not read; a self-harm phrase used as a joke still triggers. Invisible characters (zero-width marks) never hide a phrase.
- An emergency message still records the risk words it contains (insulin, pregnancy), for the next answers.

## Sign-off rules and readiness report (item F, tooling part, 16 Oct 2026)

The real limits, food list, swap table and recipes need a dietitian and a doctor. What can be built before them is the gate that
refuses unsigned data and the report that lists what is left.

```
.venv\Scripts\python.exe -m dixit_answer.readiness          # add --json for a machine-readable list; exit code 1 until everything is signed
```

- **Production refuses an unsigned limit.** At start-up (`consistency.py`, production only) every row of the limits table needs a
  real `source`, a `reviewer` and a `review_date` written YYYY-MM-DD that is not in the future. Empty text, "PLACEHOLDER", "TBD",
  "N/A", numbers and similar are refused, and so is a second row for the same condition and nutrient. Development is unchanged.
- **The report** (`readiness.py`, rules in `sign_off.py`) reads the same files and lists every open point: each data file whose status is
  TEST ONLY, STUB, PARTIAL, PROPOSED, PLACEHOLDER, DRAFT, empty or missing; each limit without source, reviewer or date; an empty
  `approved_by` or `approved_on` on the approved list and the recipes; and the manifest switches (test-only data allowed, Indian
  records without a licence ID, AI recipe drafting on before the item G review). A damaged or missing file is named, never a crash.
  It changes no file.
- Still waiting on people: the real numbers and sources, food_item v1.0 (allergen and diet tags, paneer proxy), the swap table and the
  30 to 50 approved recipes. Tests: `tests/test_readiness.py` (126).

## The 100-recipe review tool (item G, tooling part, 16 Oct 2026)

Switching on AI recipe drafting in production needs a dietitian to score 100 AI-drafted recipes first. The tool does the preparation
and the arithmetic; people do the scoring.

```
.venv\Scripts\python.exe -m dixit_answer.sample_review make  [--out var\sample_review] [--size N]
.venv\Scripts\python.exe -m dixit_answer.sample_review score SHEET.csv --sample var\sample_review\sample.json
```

- **`make`** draws different people (conditions, allergies, diet, what is in the kitchen), runs each through the real recipe flow with
  every check on, and keeps the recipes the AI drafted that passed the gate. It then re-checks every kept recipe straight from the raw
  data files (allergens, vegetarian and Jain rules, limits) without using the recipe code, and stops if one breaks a rule. It writes
  `sample.json` and `scoring_sheet.csv` (plain food names, no codes). The same seed draws the same people. It asks for the coverage the
  rules file lists (for example at least 5 people per allergy) and says in plain words which group fell short.
- **`score`** reads the filled sheet. Each recipe needs `safe` (Y or N), `sensible` and `cookable` (whole numbers 1 to 5), a reviewer name
  and a review date; a missing, doubled or unknown recipe, or a bad cell, makes the sheet INVALID. The pass mark is in
  `data/sample_review_rules.json` (SR-0.1.0, PROPOSED): no recipe unsafe, mean at least 4.0 on both scores, at least 90% scoring 3 or
  more. The sample records a fingerprint of that file, so a pass mark changed after the sample was drawn is refused. The verdict
  PASSED needs a signed rules file, otherwise it prints PASSED_UNSIGNED. It never edits `manifest.json`; switching
  `recipe_ai_drafting` on is a manual step after PASSED.
- **Today's finding:** for a person with diabetes the AI is never asked to draft, because the added-sugar limit cannot be checked (item E,
  open decision). So a sample cannot yet include diabetes, and `make` reports that shortfall instead of hiding it. Tests: `tests/test_sample_review.py` (98).

## Food list v0.3.0 (16 Oct 2026)

The food list (`food_master.json`, FM-0.3.0) grew from 42 to 77 foods: 35 more vegetables, leafy greens, roots and pulses that people cook
(capsicum, brinjal, karela, carrot, rajma, tofu and others), each mapped to one USDA record, with Hindi and regional names. Soy and chickpea
allergies block the new soy and chickpea foods, the Jain list now also excludes carrot, beetroot, mooli, shalgam, arbi, suran, spring onion and
mushroom, and the AI is never asked to build a recipe around a dry pulse (`dry_weight`). Foods with no USDA record (methi leaves, curry leaves,
tinda, parwal, gawar, snake gourd) still go to the dietitian queue. Allergens, names and unit weights need dietitian review. Tests: `tests/test_food_coverage.py`.

## Feeling anxious, stressed or low (16 Oct 2026)

A message that says how the person feels (anxious, stressed, panic, nervous, overwhelmed, low, ghabrahat and similar) gets a fixed, approved
answer from `data/support_content.json` (SC-0.1.0, PROPOSED: a doctor signs it before production): a kind line, three everyday habits taken
from the US National Institute of Mental Health fact sheet (public domain; regular healthy meals, not too much caffeine, a sleep routine), a
plain statement that food can't treat anxiety and no supplement is suggested, where to get help (a doctor or counsellor, Tele-MANAS 14416) and
112 for chest pain, trouble breathing or thoughts of self-harm. The AI is never used for it. An emergency phrase in the same message still wins.
A feeling together with a food question gets the kind line and the help line, then the normal food answer. Tests: `tests/test_wellbeing_support.py`.

## More dishes for what a person has (RCP-0.2.0, 16 Oct 2026)

The approved recipe collection grew from 8 to 77 development dishes (`recipes_test.json`, still TEST ONLY: a dietitian rewrites and signs
them): several dals and pulses (moong dal khichdi, chilla, soup, tadka, rajma, chana masala, lobia, tofu bhurji, besan chilla...), rice and suji
dishes (khichdi, pulao, upma, lemon rice), and a sabji for every vegetable in the food list, with Jain-friendly versions that use no onion,
garlic or root vegetables. When a person says what they have, the chat lists every approved dish that passes their checks (up to six, dishes
that need one more food marked "also needs"), shown as tap buttons in the chat page; one dish is shown in full. Foods with no approved dish
still get an AI-drafted, gate-checked recipe, except dry pulses (they need soaking). Moong dal halwa is in the collection only so a request
for it gets an honest answer: it is a sweet, and the Golden Rules do not allow a sweet as a home-meal dish, so it is never offered on its
own. `khichdi` and `chilla` were added as allowed home-meal dish kinds (to confirm with Dixit). Most older tests run against the first eight
recipes (`CLASSIC_RECIPES` in `tests/conftest.py`); `tests/test_recipe_variety.py` covers the full collection.

## Hindi in Devanagari script (21 Oct 2026)

`dixit_answer/hindi.py` rewrites a message before anything reads it: "पोहा में कितनी कैलोरी है" reaches the pipeline as
"how much calories in poha". Each Devanagari word is looked up in a table of everyday words (questions, nutrients, conditions,
foods); a word that is not in the table is spelled out letter by letter, so a dish name still reaches the food and dish lookup.
A few sentence shapes are rewritten as English questions (how much ... in ..., can I eat ..., recipe for ..., X or Y, allergic to ...),
and the same shapes work for Roman Hindi ("dal mein kitna protein hai"). Text with no Devanagari and no such shape is not changed.

- The safety check reads the original Devanagari text and the rewritten text together, so the Devanagari emergency phrases still
  stop the turn (chest pain, breathing, fainting, low sugar, suicide ...).
- Numbers still come from the same locked facts; the answer is in English. A word we cannot read is named in Roman letters and
  reported as an unknown item, never guessed.
- Add a word by adding it to the table in `hindi.py` (Devanagari, then the Roman word the food list already knows).
Tests: `tests/test_hindi.py`.

## A dish name on its own (21 Oct 2026)

When the whole message is a dish name ("poha", "what is masala dosa?", "tell me about upma", "poha nutrition") the chat gives that
dish's calories, the same answer as "how many calories in poha". A message with anything else in it ("I like poha", "is poha good for
me") is not taken this way. A name that fits several dishes ("biryani") asks which one is meant; a dish the person cannot have
(allergy, diet) is refused as before. Tests: `tests/test_bare_dish.py`.

## Letting a teammate open the chat (`DIXIT_HOST`, 21 Oct 2026)

By default the server listens on this machine only. Set `DIXIT_HOST=0.0.0.0` to let a teammate on the same network open it; the
start-up message prints the address to share. The chat is then open to anyone who can reach it (no sign-in, plain http), so use a trusted
network. The dietitian review pages (`/review`, `/api/review/*`) change files and answer only the computer running the server.
Steps and troubleshooting: `docs/SETUP.md` section 4. Tests: `tests/test_host.py`.

## Meaning around a food (21 Oct 2026)

`dixit_answer/semantics.py` reads what sits around the foods in "I have ...". Wishes, tastes, times and people ("quick", "spicy",
"for dinner", "my kids", "kuch light banao") are not foods, so they are no longer reported as unknown foods or sent to the dietitian
queue. "No onion", "without onion", "don't have tomato", "tamatar nahi hai", "out of onion" mean the food is NOT on hand, so it is
taken out of what the person has. Real unknown foods ("dragonfruit") are still reported. With the language model switched on, a word
that is still unknown gets one closed question (not a food / a food / the same as one food on our list): the answer can only drop a
non-food word or ask the person to confirm a food from OUR list. It never adds a food and no number comes from it. With no model it
is skipped. Tests: `tests/test_semantics.py`.

## Follow-ups use the last answer (21 Oct 2026)

A short follow-up is now read in the light of the last answer, in the same chat:

- After a recipe answer, "what else", "anything else", "another one", "something different" give a different approved dish and never
  repeat one already shown; when none is left it says so. The chat remembers the dishes shown (`recipes_shown` in the chat state).
- After a calories or protein question, "and protein?", "and its carbs", "how many calories in it" keep the same food or dish
  ("poha"); "what about dal?" asks the same nutrient about dal, or "can I eat dal" after a can-I-eat question; "is it good for
  diabetes" means the food just discussed. This applies within three turns and only to short messages that name no food of their own.
Tests: `tests/test_followups.py`.

## "Why?" and "tell me more" (21 Oct 2026)

After an answer, "why", "tell me more", "where is that from" and similar short messages get only what is already written down
(`dixit_answer/explain.py`): for a recipe, its approved reason and how the nutrition was calculated; for a number, which source it
came from; for a programme answer ("rice is allowed"), a plain "I don't have a written reason for that yet, please ask your Dixit
coach". No new claim is made. It works within three turns of the answer. Tests: `tests/test_explain.py`.
