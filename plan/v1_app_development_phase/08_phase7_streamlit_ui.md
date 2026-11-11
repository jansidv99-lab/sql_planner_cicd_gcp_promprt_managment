# Phase 7 — Streamlit UI (detailed plan)

**Status:** DRAFT, waiting for review and permission.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 6 (`chain.run`, `ChainResult`, `Status`) ✅, Phase 1 (`COLUMNS`, `MAX_REQUEST_CHARS` from the Planner) ✅, Phase 1 config (`load_settings`, `ConfigError`) ✅.
**Unblocks:** Phase 8 (test suite completion), Phase 9 (Docker image runs this app).

## Goal

A single-page Streamlit app where the user picks columns, types a request, and sees the outline, the SQL and the Reviewer's verdict. All logic stays in `app/`; the UI only collects input, calls `chain.run()` once, and renders the result.

## Screen layout

```
┌──────────────────────────────────────────────────────────┐
│ SQL Script Generator                                     │
│ Generates BigQuery SQL for the `sales` table. No data is │
│ queried; this app only writes and checks SQL.            │
│                                                          │
│ ▸ Schema: sales   (table: column | type | description)   │
│                                                          │
│ ┌ form ────────────────────────────────────────────────┐ │
│ │ Columns  [order_date ✕] [amount ✕]          ▾       │ │
│ │ Request  ┌──────────────────────────────┐  31/1000  │ │
│ │          │ total sales by month         │           │ │
│ │          └──────────────────────────────┘           │ │
│ │ [ Generate SQL ]                                     │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ✅ SQL passed all checks          (or ❌ / ⚠️ + message)  │
│                                                          │
│ 1. Plan       numbered outline                           │
│ 2. SQL        formatted SQL (copy button)  [Download .sql]│
│ 3. Review     list of Reviewer errors (when failed)      │
└──────────────────────────────────────────────────────────┘
```

---

## Steps

### Step 7.1 — Page header and schema panel
- **Why:** users must see exactly which table and columns exist before choosing; this reinforces "only these columns".
- **What:** `st.set_page_config(page_title="SQL Script Generator")`, a title, a one-line description, and a small table built from `schema.COLUMNS` (name, BigQuery type, description).
- **Depends on:** Phase 1 (`COLUMNS`).

### Step 7.2 — Configuration check at startup
- **Why:** if `.env` / Cloud Run env vars are missing, show one clear message instead of failing on the first click.
- **What:** call `load_settings()` once at startup; on `ConfigError` show `st.error("App is not configured: Missing required settings: …")` and `st.stop()`. The message names the missing variables, never the values.
- **Depends on:** Phase 1 (config).

### Step 7.3 — Input form
- **Why:** a form sends everything in one go when the button is pressed; without it Streamlit reruns on every keystroke.
- **What** (inside `st.form`):
  - **Columns:** `st.multiselect` with options = `schema.COLUMN_NAMES` only. The user **can't type** a column name, only pick one of the 4.
  - **Request:** `st.text_area` with `max_chars=1000` (Streamlit shows a live counter and blocks extra characters; matches the Planner's limit), with a placeholder example such as "Top 5 products by total sales".
  - **Generate SQL** submit button.
- **Depends on:** Phase 1, Phase 3 (`MAX_REQUEST_CHARS`).

### Step 7.4 — Run the chain
- **Why:** one call does all the work; the UI holds no chain logic.
- **What:** on submit: `with st.spinner("Planning → writing SQL → reviewing…"):` `result = chain.run(request, columns)`. Store `result` in `st.session_state` so it stays on screen when the page reruns (e.g. after clicking Download).
  - Empty-column and empty-request cases are **not** special-cased in the UI: the chain already returns `input_error` with a friendly message without calling the LLM.
- **Depends on:** Phase 6.

### Step 7.5 — Render the result by status
- **Why:** the user should immediately see whether the SQL is valid and, if not, why.
- **What:**
  | Status | Badge | Panels shown |
  |---|---|---|
  | `ok` | `st.success` "✅ SQL passed all checks" | Plan, SQL, Download |
  | `review_failed` | `st.error` "❌ The generated SQL failed the review checks" | Plan, SQL (labelled "not valid, shown for reference"), Review errors as a list |
  | `cannot_answer` | `st.warning` with the model's reason + hint "Try selecting more columns" | none |
  | `input_error` | `st.warning` with the message | none |
  | `coder_error` | `st.error` with the message | Plan |
  | `llm_error` | `st.error` with the message | Plan (if the Planner finished) |
  - SQL is shown with `st.code(review.formatted_sql or sql, language="sql")`, which includes a copy button.
  - **Download .sql** (`st.download_button`, file `query.sql`) **only when status is `ok`**, so invalid SQL is never offered as a file.
- **Depends on:** 7.4.

### Step 7.6 — Testability
- **Why:** the UI is tested with Streamlit's built-in `AppTest` (available in the installed `streamlit==1.67.0`), which runs the script in-process without a browser.
- **What:** `streamlit_app.py` imports the module (`from app import chain`) and calls `chain.run(...)`, so tests can replace `chain.run` with a fake that returns a chosen `ChainResult`. Same for `load_settings`.
- **Depends on:** 7.1–7.5.

### Step 7.7 — Tests (`tests/test_streamlit_app.py`)
- **Why:** prove each status renders correctly, with no real API calls.
- **Cases:**
  1. page loads: title, schema table, multiselect offers exactly the 4 columns;
  2. missing config → error message naming the variables, form not shown;
  3. `ok` result → success badge, outline, SQL code block, download button present;
  4. `review_failed` → error badge, every Reviewer error listed, **no** download button;
  5. `cannot_answer` → warning with the reason, no SQL shown;
  6. `llm_error` → error message shown;
  7. submitting passes the selected columns and request text to `chain.run` exactly once.
- **Depends on:** 7.6.

### Step 7.8 — Manual check (local run)
- **Why:** see the real app working in your browser.
- **What:** I start `streamlit run streamlit_app.py` in the background and confirm it answers on `http://localhost:8501` (health endpoint `/_stcore/health`). **You** then open it and try a few requests. Each "Generate SQL" click makes 2 real API calls.
- **Depends on:** 7.1–7.5.

### Step 7.9 — Verify and commit
- `ruff check .` and `pytest` pass.
- Update `CLAUDE.md` (run command is already listed; mark the UI as built).
- Commit `"Phase 7: Streamlit UI"`.
- **Depends on:** 7.7 (and 7.8).

---

## Step dependencies

```
7.1 ─┐
7.2 ─┼─► 7.4 ─► 7.5 ─► 7.6 ─► 7.7 (tests) ─┐
7.3 ─┘                  └──► 7.8 (manual) ─┴─► 7.9
```

## Files changed in this phase

- `streamlit_app.py` (placeholder replaced)
- `tests/test_streamlit_app.py` (new)
- `CLAUDE.md` (small status update)

Logging setup is **not** part of this phase; it's in Phase 12.

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Column picker default: **none selected** or **all 4 selected**? | **None selected**: the user consciously picks columns (the core anti-hallucination idea); an empty selection gets a friendly "Select at least one column" |
| 2 | Offer a **Download .sql** button (only for SQL that passed review)? | **Yes** |
| 3 | When review fails, still **show the invalid SQL** (clearly labelled) so the user can see what went wrong? | **Yes**: shown for reference with the errors; not downloadable |
| 4 | Run the manual check (I start the app; you click; 2 real API calls per click)? | **Yes** |
