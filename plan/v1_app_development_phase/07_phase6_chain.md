# Phase 6 — Chain Orchestrator (detailed plan)

**Status:** APPROVED (status + message; logging/timings moved to Phase 12; manual check approved).
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 3 (`make_plan`) ✅, Phase 4 (`write_sql`) ✅, Phase 5 (`review_sql`) ✅, Phase 2 (`complete`, `LLMError`) ✅, Phase 1 (`validate_selection`, `SchemaError`) ✅.
**Unblocks:** Phase 7 (Streamlit UI): the UI calls one function and renders its result.

## Goal

Connect the three steps into **one call**:

```
run(request, columns)
  │
  ├─ validate columns ── bad ──────────────► status = input_error
  ├─ [1] Planner ─────── empty/too long ───► status = input_error
  │                  └── CANNOT_ANSWER ────► status = cannot_answer   (outline: none)
  ├─ [2] Coder ───────── no SQL ───────────► status = coder_error     (outline kept)
  ├─ [3] Reviewer ────── fails ────────────► status = review_failed   (outline + SQL + errors kept)
  │
  └─ all pass ─────────────────────────────► status = ok
  (an LLM failure in step 1 or 2 ──────────► status = llm_error)
```

Each step runs **once**: no retries (your decision). The UI never needs `try/except`: every expected failure comes back as a status plus a friendly message.

---

## Steps

### Step 6.1 — `Status` values
- **Why:** the UI picks what to show (green/red badge, which panels) from one field.
- **What** (in `app/chain.py`): a `str` enum:
  | Status | Meaning | Comes from |
  |---|---|---|
  | `ok` | SQL passed every Reviewer check | — |
  | `review_failed` | SQL generated but failed the gate | `ReviewResult.passed == False` |
  | `cannot_answer` | the selected columns can't answer the request | `CannotAnswerError` |
  | `input_error` | empty/too-long request or bad column selection | `PlannerError`, `SchemaError` |
  | `coder_error` | the Coder returned no SQL | `CoderError` |
  | `llm_error` | the LLM call failed (auth, network, timeout…) | `LLMError` |
- **Depends on:** nothing.

### Step 6.2 — `ChainResult`
- **Why:** one object carrying everything the UI shows, including **partial** results (e.g. the outline when the Coder fails), so the user can see how far it got.
- **What:**
  ```python
  @dataclass(frozen=True)
  class ChainResult:
      status: Status
      message: str | None          # friendly error text; None when ok
      columns: tuple[str, ...]     # validated selection (empty on input_error)
      outline: str | None          # Planner output
      sql: str | None              # Coder output, as returned
      review: ReviewResult | None  # Reviewer verdict (errors + formatted SQL)

      @property
      def passed(self) -> bool: return self.status is Status.OK
  ```
- **Depends on:** 6.1, Phase 5 (`ReviewResult`).

### Step 6.3 — `run()`
- **Why:** the single entry point the UI calls.
- **What:**
  ```python
  def run(request: str, selected_columns: Iterable[str], llm: LLMFn = complete) -> ChainResult
  ```
  1. `validate_selection(selected_columns)`
  2. `make_plan(request, columns, llm)`
  3. `write_sql(outline, columns, llm)`
  4. `review_sql(sql, columns)` → `ok` or `review_failed`
  - Catches **only the expected errors** in the table above and turns them into a status + message. Any other exception is a bug and is **not** hidden: it propagates so it shows up in logs and tests.
  - The same `llm` function is passed to the Planner and Coder, so tests inject one fake for both.
- **Depends on:** 6.1, 6.2, Phases 1–5.

### Step 6.4 — Logging (moved)
- Logging, per-step timings and tracing were moved to **Phase 12 — Logging and tracing** (see the master plan), at your request.

### Step 6.5 — Tests (`tests/test_chain.py`)
- **Why:** prove each path through the chain, without real API calls.
- **How:** a scripted fake LLM that returns replies in order (1st call = Planner reply, 2nd = Coder reply) and records calls.
- **Cases:**
  1. **ok**: valid outline + valid SQL → `ok`, `passed`, outline/sql/review set, **2** LLM calls;
  2. **review_failed**: Coder returns SQL using an unselected column → errors listed, outline + SQL kept;
  3. **cannot_answer**: Planner replies `CANNOT_ANSWER: …` → reason in message, **1** LLM call, no SQL;
  4. **input_error (request)**: empty request → **0** LLM calls;
  5. **input_error (columns)**: unknown column → **0** LLM calls;
  6. **coder_error**: Coder returns empty → outline kept, no review;
  7. **llm_error** in the Planner → 1 call; **llm_error** in the Coder → outline kept;
  8. an unexpected exception (e.g. `ZeroDivisionError` from the fake) **propagates**;
- **Depends on:** 6.1–6.3.

### Step 6.6 — Manual check (real calls)
- **Why:** first true end-to-end run of the whole chain against `gpt-4o-mini`.
- **What:** one-off command (not committed):
  - `product, amount` + "top 5 products by total sales" → expect `ok` (2 calls);
  - `product, amount` + "monthly sales trend" → expect `cannot_answer` (no `order_date` selected; 1 call).
- **Cost:** 3 tiny real API calls. **I'll ask before running them.**
- **Depends on:** 6.3.

### Step 6.7 — Verify and commit
- `ruff check .` and `pytest` pass.
- Commit `"Phase 6: chain"`.
- **Depends on:** 6.5 (and 6.6 if approved).

---

## Step dependencies

```
6.1 ─► 6.2 ─► 6.3 ─┬─► 6.5 (tests) ───────────────┐
                    └─► 6.6 (manual, with your OK) ─┴─► 6.7
```

## Files changed in this phase

- `app/chain.py` (placeholder replaced)
- `tests/test_chain.py` (new)

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Failures returned as status + message? | **Yes** ✅ |
| 2 | Timings and logging? | **Moved to Phase 12** ✅ |
| 3 | Manual check (3 real calls)? | **Yes** ✅ |
