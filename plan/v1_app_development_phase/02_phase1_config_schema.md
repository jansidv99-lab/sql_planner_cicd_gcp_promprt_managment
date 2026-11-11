# Phase 1 — Config and Schema (detailed plan)

**Status:** DRAFT, waiting for review and permission.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 0 (venv, packages, placeholder files) ✅ done.
**Unblocks:** Phase 2 (LLM client needs config), Phases 3–4 (prompts need the schema block), Phase 5 (Reviewer needs the allowed table and columns), Phase 7 (UI builds its column picker from the schema).

## Goal

1. **Config:** one place that reads the LLM settings and fails early with a clear message if any are missing.
2. **Schema:** one definition of the `sales` table that every other part uses (prompts, Reviewer, UI), so the allowed columns can't drift apart.

No LLM calls and no network in this phase. Everything is plain Python and fully unit-tested.

---

## Part A — `app/config.py`

### Step 1.1 — `Settings` object
- **Why:** later code receives one typed object instead of reading environment variables everywhere.
- **What:** a frozen dataclass:
  ```python
  @dataclass(frozen=True)
  class Settings:
      openai_api_key: str
      openai_base_url: str
      model: str
  ```
  `GCP_PROJECT_ID` / `GCP_PROJECT_NUMBER` are **not** included: the running app doesn't need them; only the CD pipeline does (Phase 11).
- **Depends on:** nothing.

### Step 1.2 — `load_settings()`
- **Why:** reads the settings in the same way locally (from `.env`) and on Cloud Run (real env vars, no `.env` file).
- **What:**
  ```python
  def load_settings(env: Mapping[str, str] | None = None) -> Settings
  ```
  - If `env` is `None`: call `load_dotenv()`, which **doesn't override** variables that are already set (important on Cloud Run), then read `os.environ`.
  - If `env` is given: use it directly and skip `.env`. This lets tests pass fake values without touching your real `.env`.
  - Treat a variable as missing if it's absent or blank. Raise `ConfigError` naming **all** missing variables in one message, e.g. `Missing required settings: OPENAI_API_KEY, MODEL`.
  - Never include secret values in error messages or logs.
- **Depends on:** 1.1.

### Step 1.3 — `ConfigError`
- **Why:** the UI (Phase 7) can catch this specific error and show a friendly "app not configured" message.
- **What:** `class ConfigError(RuntimeError)`.
- **Depends on:** nothing (used by 1.2).

---

## Part B — `app/schema.py`

### Step 1.4 — Schema definition
- **Why:** the single source of truth that prevents hallucinated tables/columns.
- **What:**
  ```python
  @dataclass(frozen=True)
  class Column:
      name: str
      sql_type: str
      description: str

  TABLE_NAME = "sales"
  COLUMNS: tuple[Column, ...] = (...)
  COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in COLUMNS)
  ```
  | Column | Type (BigQuery) | Description |
  |---|---|---|
  | `order_date` | `DATE` | Date the order was placed |
  | `quantity` | `INT64` | Number of units ordered |
  | `product` | `STRING` | Product name |
  | `amount` | `NUMERIC` | Order value (currency) |

  ⚠️ **Change from the master plan:** the master plan used generic types (`INTEGER`, `VARCHAR`, `DECIMAL(10,2)`). Because the dialect is BigQuery, I propose the BigQuery type names above so the LLM sees types that match the SQL it must write. If you approve, I'll update the table in `00_master_plan.md` to match.
- **Depends on:** nothing.

### Step 1.5 — `validate_selection(columns)`
- **Why:** guarantees that only real schema columns reach the prompts and the Reviewer, even if the UI is bypassed.
- **What:**
  ```python
  def validate_selection(columns: Iterable[str]) -> tuple[str, ...]
  ```
  | Input | Behaviour |
  |---|---|
  | empty selection | raise `SchemaError("Select at least one column")` |
  | unknown column(s) | raise `SchemaError` naming them, e.g. `Unknown column(s): region` |
  | duplicates | removed |
  | any order | returned in **schema order**, so prompts are always consistent |
  | case | must match exactly (lowercase names); `ORDER_DATE` is rejected |
- **Depends on:** 1.4, 1.6.

### Step 1.6 — `SchemaError`
- **Why:** the UI and chain can tell "bad column selection" apart from other failures.
- **What:** `class SchemaError(ValueError)`.

### Step 1.7 — `schema_for_prompt(columns)`
- **Why:** gives the Planner and Coder prompts (Phases 3–4) one identical description containing **only** the selected columns.
- **What:** validates via 1.5, then returns text like:
  ```
  Table: sales
  Columns (use ONLY these; no other tables or columns exist):
  - order_date DATE -- Date the order was placed
  - amount NUMERIC -- Order value (currency)
  ```
- **Depends on:** 1.4, 1.5.

---

## Part C — Tests

### Step 1.8 — `tests/test_config.py`
- **Why:** proves that setup errors are caught early and reported clearly.
- **Cases:** all three present → `Settings` returned; one missing → `ConfigError` naming it; several missing → all named in one message; blank value counts as missing; error message does not contain the API key value.
- All tests pass a fake `env` dict, so they never read your real `.env`.
- **Depends on:** 1.1–1.3.

### Step 1.9 — `tests/test_schema.py`
- **Why:** this is the anti-hallucination guard, so its rules must be locked in by tests.
- **Cases:** schema has exactly the 4 expected columns; valid subset accepted; result in schema order; duplicates removed; empty rejected; unknown column rejected; wrong case rejected; prompt text includes the selected columns and their types; prompt text **excludes** unselected columns.
- **Depends on:** 1.4–1.7.

### Step 1.10 — Verify and commit
- `ruff check .` and `pytest` pass (pytest should now exit 0).
- Commit `"Phase 1: config and schema"`.
- **Depends on:** 1.1–1.9.

---

## Step dependencies

```
1.1 ─┐
1.3 ─┴─► 1.2 ─────────────► 1.8 ─┐
1.4 ─┐                           ├─► 1.10
1.6 ─┴─► 1.5 ─► 1.7 ──────► 1.9 ─┘
```
Part A (config) and Part B (schema) don't depend on each other.

## Files changed in this phase

- `app/config.py`, `app/schema.py` (placeholders replaced with real code)
- `tests/test_config.py`, `tests/test_schema.py` (new)
- `plan/00_master_plan.md` (schema types table, only if 1.4's type change is approved)

## Decision needed

- **Column types:** use BigQuery type names (`DATE`, `INT64`, `STRING`, `NUMERIC`) as proposed in Step 1.4? *(Recommended: yes)*
