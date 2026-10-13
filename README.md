# Dixit answer pipeline

Swastham's own chat answer system for the Dixit Lifestyle app, built from
`Documents\Dixit_Chat_Answer_Architecture.html` (design) and `Documents\Dixit_Chat_Answer_Analysis.html` (problems P1–P14, D1–D3).
Standalone Python package until the Dixit app code is available. **Development build: phases 0–4 (core pipeline).**

## Run

```
.venv\Scripts\python.exe -m pytest -q              # 46 tests, a few seconds
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
| 6 Fetch | `services.py`, `facts.py` | Golden Rules stub, approved food list, cooking tips, nutrition facts |
| 7 Compose | `compose.py`, `llm.py` | Template with locked items; optional AI rewording |
| 8 Check | `checks.py` | N1/N15, N4, N6, N9, N16, N17c, N18–N23, N27, placeholders, length |
| 9 Deliver + log | `pipeline.py` | Append-only `var/audit.jsonl` (versions, plan, records, checks, final text) |
| 10 Learn | `web.py` | Feedback buttons → audit log |
| Data | `loader.py`, `data/` | Hash-pinned library, environment switches, quality gates D1–D3 |

## TEST ONLY data (`dixit_answer/data/`)

`food_master.json`, `approved_list_test.json`, `cooking_tips_test.json`, `word_lists.json` are drafts; `golden_rules_stub.json`
only repeats what the live Dixit app said on 13 Oct 2026. Production refuses all of them until a dietitian / Dixit sign them.
The nutrition library is read in place from `Desktop\Nutrition\Swastham_Nutrition_Data_Library_v1_0_3` and checked against
the hashes in `data/manifest.json`. Production loads USDA only.

## Not built yet

- Phase 5: allergy and avoid filter (N24), hybrid recipe flow R1–R9 (approved recipes, AI draft + recipe gate, repair,
  fallback), condition limits, food master unit weights, N25–N26.
- Phase 6: real Golden Rules (base package v1.1 + v1.1.1).
- Phase 7: connecting to the Dixit app. Phase 8: dietitian / doctor / legal review.
