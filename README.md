# Dixit answer pipeline

Swastham's own chat answer system for the Dixit Lifestyle app, built from
`Documents\Dixit_Chat_Answer_Architecture.html` (design) and `Documents\Dixit_Chat_Answer_Analysis.html` (problems P1–P14, D1–D3).
Standalone Python package until the Dixit app code is available. **Development build: phases 0–5 (core pipeline + allergy filter + hybrid recipes).**

## Run

```
.venv\Scripts\python.exe -m pytest -q              # 88 tests, about 15 seconds (no AI calls; the AI is mocked)
.venv\Scripts\python.exe -m dixit_answer.web       # chat page on http://127.0.0.1:8100
```

Port 8100 so it never clashes with the Swastham website servers (8000, 5173).
Set-up on a new machine: `python -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`
(on this PC, run pip with `CURL_CA_BUNDLE` cleared). Copy `.env.example` to `.env`.

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
| 3 Safety gate | `safety.py` | Emergency phrases; risk words (insulin, pregnancy, kidney…) with negation |
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
The nutrition library is read in place from `Desktop\Nutrition\Swastham_Nutrition_Data_Library_v1_0_3` and checked against
the hashes in `data/manifest.json`. Production loads USDA only.

## Recipes (phase 5)

Approved recipes first, AI-drafted + gated second, fallback last (`recipes.py`):

R1 map pantry words (unknown words are left out, queued for the dietitian, never sent to the AI; a close match is confirmed first)
→ R2 allergy / avoid / diet filter (derivatives such as ghee and paneer for milk) → R3 limits per meal
→ R4 approved recipe match → R5 AI draft (only food IDs + grams + steps) → R6 calculate the whole dish from USDA values
(exact decimals, missing value = error) → R7 gate (allergens, Golden Rules, 90% of one meal's share of each limit) → R8 repair one
ingredient (never scale the dish), then AI retry (max 2) → R9 fallback (partly matching approved recipe, or tips).

Nothing unchecked is shown. Limits are never shown to the person. AI drafting is switched off in production in `manifest.json`
until a dietitian has reviewed a sample and the limits are signed, and it is also off whenever allergies are unknown or a
limit can't be verified (sodium and sugar are not in the library yet).

## TEST ONLY data added in phase 5

`recipes_test.json` (8 recipes), `allergen_map.json`, `condition_limits_test.json` (**placeholder numbers**),
`swap_table_test.json`, `cooking_factors.json`, and food master v0.2 (ingredient records, unit weights, pantry items).
Paneer uses USDA queso fresco as a PROXY record until a dietitian confirms a better one.

## Not built yet

- Phase 6: real Golden Rules (base package v1.1 + v1.1.1).
- Phase 7: connecting to the Dixit app. Phase 8: dietitian / doctor / legal review.
- Sodium and sugar for USDA records (library v1.0.4), so blood-pressure and diabetes limits can be checked.
- Fixes D1–D3 in the Indian library itself (the loader's quality gates only block the bad records).
