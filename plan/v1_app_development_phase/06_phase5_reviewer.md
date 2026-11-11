# Phase 5 — Reviewer / Gate Check (detailed plan)

**Status:** DRAFT, waiting for review and permission.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 1 (`TABLE_NAME`, `COLUMN_NAMES`, `validate_selection`) ✅. Doesn't use the LLM.
**Unblocks:** Phase 6 (chain): the Reviewer's verdict decides whether SQL is shown as valid.

## Goal

Chain step 3: a **deterministic** check (no LLM, same answer every time) that the Coder's SQL:
1. parses as BigQuery SQL, and
2. stays inside the schema: only the `sales` table, only the columns the user selected, read-only.

If any check fails, the user sees the list of errors and the SQL isn't marked as valid. **No retries** (your decision).

---

## Findings from testing the installed `sqlglot==30.30.0`

These shaped the rules below:

| SQL | What sqlglot does | Consequence for the Reviewer |
|---|---|---|
| `SELEC product FROM sales` | parse error ✅ | syntax errors are caught |
| `SELECT product FROM sales WHERE` | parse error ✅ | incomplete SQL is caught |
| `hello world` | **parses** (as an alias expression) | must also check the statement **type** is a query |
| `DROP TABLE sales` | parses as `Drop` | must check the type: only `SELECT` / set queries |
| `SELECT 1; SELECT 2` | 2 statements | must count statements |
| `SELECT product FROM sales;` | 1 statement (trailing `;` is fine) | allowed |
| `SELECT * FROM sales` | contains a `Star` | reject |
| `SELECT COUNT(*) FROM sales` | **also** contains a `Star` (inside `COUNT`) | allow `*` only inside `COUNT(*)` |
| `WITH t AS (…) SELECT … FROM t` | table list includes the CTE name `t` | CTE names must be allowed as tables |
| `SELECT s.product FROM sales AS s` | column `s.product` | table aliases of `sales` must be allowed |
| `` FROM `proj.ds.sales` `` | table name `sales` with project/dataset | decide whether to allow (Decision 2) |
| `ORDER BY total_sales` | `total_sales` looks like a column | aliases defined in the query must be allowed |

---

## Steps

### Step 5.1 — `ReviewResult`
- **Why:** one simple object the chain and UI can read.
- **What** (in `app/reviewer.py`):
  ```python
  @dataclass(frozen=True)
  class ReviewResult:
      passed: bool
      errors: tuple[str, ...]   # empty when passed
      formatted_sql: str | None # pretty-printed SQL when it parsed, else None
  ```
- **Depends on:** nothing.

### Step 5.2 — Check 1: parses
- **Why:** the core gate from the project goal: "checks that the code parses".
- **What:** `sqlglot.parse(sql, read="bigquery")`, raising on errors. On `ParseError` → fail immediately with `Syntax error: <first line of sqlglot's message>` (the other checks need a parsed tree, so they're skipped).
- Empty / whitespace input → fail: `No SQL to review`.
- **Depends on:** 5.1.

### Step 5.3 — Check 2: exactly one statement
- **Why:** blocks "valid query; then something else" tricks.
- **What:** ignore empty statements (trailing `;`); 0 or 2+ statements → `Expected exactly one statement, found N`.
- **Depends on:** 5.2.

### Step 5.4 — Check 3: read-only query
- **Why:** the app must only ever produce `SELECT` queries: no `INSERT/UPDATE/DELETE/DROP/CREATE/MERGE`, and no non-query text like `hello world`.
- **What:** the statement must be a `SELECT`, or a set query (`UNION / INTERSECT / EXCEPT`) built from `SELECT`s. Otherwise → `Only SELECT queries are allowed (got DROP)`.
- **Depends on:** 5.3.

### Step 5.5 — Check 4: tables
- **Why:** anti-hallucination: the only table that exists is `sales`.
- **What:** every table in the query (including inside subqueries) must be `sales` or the name of a CTE defined in the same query. Otherwise → `Unknown table: customers`.
- **Depends on:** 5.4.

### Step 5.6 — Check 5: no `*` wildcard
- **Why:** `SELECT *` (or `s.*`) would include columns the user didn't select.
- **What:** reject any `*` except inside `COUNT(*)` → `SELECT * is not allowed; list the columns explicitly`.
- **Depends on:** 5.4.

### Step 5.7 — Check 6: columns
- **Why:** anti-hallucination: only the columns the user selected may be used.
- **What:**
  - Allowed names = the user's selected columns + aliases defined in the query (e.g. `total_sales`, CTE output columns).
  - Every column reference must be in the allowed names → otherwise `Column not allowed: region` (unknown) or `Column not selected: quantity` (exists in the schema but wasn't selected; a more helpful message).
  - A column qualifier (`s.product`, `sales.product`) must be `sales`, an alias of `sales`, or a CTE name.
  - Comparison is **case-insensitive** (`Amount` = `amount`), because BigQuery column names are case-insensitive.
- **Depends on:** 5.4, Phase 1 (`validate_selection` for the selection itself).

### Step 5.8 — `review_sql()`
- **Why:** the single entry point the chain calls.
- **What:**
  ```python
  def review_sql(sql: str, selected_columns: Iterable[str]) -> ReviewResult
  ```
  - Validates the selection (`SchemaError` for a bad one).
  - Runs checks 1→6. If parsing fails it stops; otherwise it **collects all errors** from checks 2–6, so the user sees every problem at once.
  - On parse success, `formatted_sql` = pretty-printed BigQuery SQL (multi-line, readable in the UI) whether or not later checks pass.
- **Depends on:** 5.1–5.7.

### Step 5.9 — Tests (`tests/test_reviewer.py`)
- **Why:** this is the safety gate, so every rule is pinned down by a test. No LLM involved, so tests are plain input → result.
- **Pass cases:** the real SQL from the Phase 4 manual check; `COUNT(*)`; aliases in `ORDER BY`; `WHERE`/`GROUP BY`/`HAVING`; table alias (`sales AS s`); CTE; `UNION ALL`; trailing `;`; different case (`AMOUNT`).
- **Fail cases (one per rule):** syntax error; empty input; two statements; `DROP`/`DELETE`/`INSERT`; `hello world`; unknown table; table inside a subquery; `SELECT *`; `s.*`; unselected schema column; unknown column; bad qualifier (`other.amount`).
- **Also:** several problems → all errors listed; `formatted_sql` is set when parsing succeeds and `None` when it fails; invalid selection → `SchemaError`.
- **Depends on:** 5.8.

### Step 5.10 — Verify and commit
- `ruff check .` and `pytest` pass. No real API calls in this phase.
- Commit `"Phase 5: reviewer"`.
- **Depends on:** 5.9.

---

## Step dependencies

```
5.1 ─► 5.2 ─► 5.3 ─► 5.4 ─┬─► 5.5 ─┐
                          ├─► 5.6 ─┼─► 5.8 ─► 5.9 ─► 5.10
                          └─► 5.7 ─┘
```
Checks 4, 5 and 6 are independent of each other; all need a parsed, single, read-only statement.

## Files changed in this phase

- `app/reviewer.py` (placeholder replaced)
- `tests/test_reviewer.py` (new)

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Allow **CTEs (`WITH`)** and **set queries (`UNION` etc.)**, as long as every part reads only `sales` with selected columns? | **Yes**: still read-only and inside the schema; the model sometimes uses them for "top N per group" style requests |
| 2 | Allow a project/dataset prefix on the table (`` `proj.ds.sales` ``)? | **No**: only plain `sales`, matching the schema exactly |
| 3 | Return **pretty-printed** SQL (multi-line) for the UI? | **Yes**: the logic is unchanged, only the layout |
