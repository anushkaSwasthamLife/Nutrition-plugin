# Setting up the Nutrition plugin on another machine

The code is in this repo. The nutrition data is **not**, so the chat cannot start from the repo alone. This page lists everything
you need and how to check that it works.

## 1. What you need

| Item | Notes |
|---|---|
| Python **3.10 or newer** | Tested on 3.13. Download from python.org. |
| This repo | `git clone` it anywhere, for example `D:\work\Nutrition-plugin`. |
| The **data folder** | Not in git (see section 3). Ask the project owner for it. |

No AI key is needed. Without one, the chat uses its plain template wording.

## 2. Install

In the project folder:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

On macOS or Linux use `.venv/bin/python` instead of `.venv\Scripts\python.exe`. If `pip` fails with a certificate error on a
company laptop, clear the variable `CURL_CA_BUNDLE` for that command.

## 3. The data folder

Put it **next to** the project folder, with this name and layout:

```
D:\work\
  Nutrition-plugin\                     <- this repo
  Nutrition\                            <- the data folder
    Swastham_Nutrition_Data_Library_v1_0_4\      REQUIRED: the chat will not start without it
      01_data\  (the .jsonl files)
    indb_source\                                  optional: dish answers such as "calories in poha"
      recipes.xlsx  INDB.xlsx  UK_fct.xlsx  US_fct.xlsx  recipes_names.xlsx  recipes_servingsize.xlsx
    releases\2026.10.16.2\                        optional, same purpose
      ingredient_map.csv  alias_table.csv
```

Size: the library is about 34 MB; the optional INDB files are about 3 MB.

If you keep the data somewhere else, set two paths in `.env`:

```
DIXIT_NUTRITION_DIR=D:\data\Nutrition
DIXIT_LIBRARY_DIR=D:\data\Nutrition\Swastham_Nutrition_Data_Library_v1_0_4
```

The library must be **exactly version 1.0.4**: the start-up check compares each file with a fingerprint stored in
`dixit_answer/data/manifest.json` and refuses a different copy.

**Do not commit the data folder to git.** Its Indian records come from NIN-derived sources whose licence for commercial use is not
yet confirmed. Share it through a private channel. (`.gitignore` already ignores a `Nutrition/` or `nutrition_data/` folder inside
the project, in case you keep it there.)

## 4. Run

```
.venv\Scripts\python.exe -m dixit_answer.web
```

Open **http://127.0.0.1:8100**. The port is 8100 so it never clashes with the Swastham website (8000, 5173). The server listens on
this machine only (`127.0.0.1`); it is a development tool, not a deployment.

Run the tests (about 6 to 15 minutes, no AI calls):

```
.venv\Scripts\python.exe -m pytest -q
```

## 5. Check that it works

1. The page loads, and `http://127.0.0.1:8100/api/info` shows `"environment":"development"` and library version `1.0.4`.
2. Ask: **how many calories in a plate of poha**. Expected: *A bowl of poha has about 161 calories. (dev data)*
3. Ask: **calories in cabbage rolls**. Expected: it asks which one (dry or curry).

If step 2 gives "I couldn't find that food in our data yet", the INDB files are missing or in the wrong place (dish answers are off
but everything else works).

## 6. Settings

Set them in `.env` or as environment variables.

| Setting | Default | Meaning |
|---|---|---|
| `DIXIT_NUTRITION_DIR` | `../Nutrition` | Folder with `indb_source` and `releases` |
| `DIXIT_LIBRARY_DIR` | `../Nutrition/Swastham_Nutrition_Data_Library_v1_0_4` | The library (required) |
| `DIXIT_ENVIRONMENT` | `development` | `production` refuses all TEST ONLY and PROPOSED data, so it will not start until reviewers have signed everything |
| `DIXIT_PORT` | `8100` | Chat page port |
| `DIXIT_DISH_LOOKUP` | `on` | `off` skips INDB dish answers |
| `DIXIT_VAR_DIR` | `./var` | Chat state (SQLite) and the audit log |
| `LLM_ENV_FILE`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY` | mock | Optional AI wording (Groq `openai/gpt-oss-120b`) |

## 7. Useful commands

```
.venv\Scripts\python.exe -m dixit_answer.readiness        # what is still unsigned before production
.venv\Scripts\python.exe -m dixit_answer.indb_compare     # re-run the INDB comparison (needs the INDB files)
.venv\Scripts\python.exe -m dixit_answer.reader_test      # write a sheet of answers for non-expert readers
.venv\Scripts\python.exe -m dixit_answer.audit_report --date latest
```

## 8. Troubleshooting

| Message | Cause and fix |
|---|---|
| `LoadError: Library file missing: ...` | The data folder is not where the project looks. Check section 3 or set `DIXIT_LIBRARY_DIR`. |
| `LoadError: Hash mismatch for ...` | The library is not v1.0.4, or a file was changed. Get a fresh copy. |
| `LoadError: Production refuses ...` | You set `DIXIT_ENVIRONMENT=production` before the data was signed. Use `development`. |
| Port already in use | Another program has 8100. Set `DIXIT_PORT`. |
| Tests are very slow | Antivirus or file syncing (OneDrive) scanning the `.venv` folder. Exclude it. |
