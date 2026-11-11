# Phase 17 — Few-shot examples (detailed plan)

**Status:** IN PROGRESS. Code done on `feature/few-shot-examples`; gate green (238 tests, 100 % coverage); v1/v2 golden files unchanged; `check_templates()` → 14 files, versions v1, v2, v3. Waiting on CI, merge and the CD check (production stays on v1).

**Implementation notes:**
- `examples.json` has 7 examples (6 answers + 1 refusal), written to **follow the v2 hints** (ROUND money, snake_case aliases, DATE literals), so v3 never contradicts v2.
- v1 gets an empty `{% block examples %}`; `v3/*` extends v2 and fills it. The chain is v3 → v2 → v1.
- `select_examples(columns, with_sql=False, k=3)`: the subset rule, first k in file order. The Planner may get the refusal example (shown as `<reply>CANNOT_ANSWER: …</reply>`); the Coder (`with_sql=True`) never does.
- Whitespace: the loop uses `{%- endfor %}` plus a `"\n\n"` separator, so examples are blank-line separated with no trailing newline (checked with `repr` before saving the golden files).
- Approximate system-prompt size (chars/4) with all 4 columns: Planner v1 211 → v2 292 → **v3 498** tokens; Coder 103 → 158 → **466**. With `order_date` alone no example matches, so v3 = v2. Phase 18 measures whether the extra tokens pay off.
**Part of:** [00_master_plan.md](00_master_plan.md) — prompt-engineering learning track
**Depends on:** Phase 16 (versions, `examples` block slot, `PromptSet`).
**Unblocks:** Phase 18 (v3 is the third candidate in the eval).
**Branch:** `feature/few-shot-examples`

## Goal

Add **v3**: v2 plus a few worked examples (request → outline for the Planner, outline → SQL for
the Coder). Examples are kept as **data**, chosen per request, and **checked by the test suite**,
so they can't silently teach the model something wrong.

## What you will learn

| Concept | Where |
|---|---|
| Few-shot prompting | Showing the model 1–3 solved cases often fixes format and style problems better than more rules |
| Example selection | Not every example fits every request: here the selection depends on the selected columns |
| Cost against quality | Each example costs prompt tokens on **every** call; Phase 18 measures whether it pays off |
| Examples are code | Wrong examples are worse than none, so they're validated by tests like any other code |

## Design

### 17.1 Example data
`app/prompt_templates/examples.json` (JSON, so no new dependency), about 6–8 entries:
```json
{"id": "total_by_product",
 "columns": ["product", "amount"],
 "request": "Total amount per product, highest first",
 "outline": "1. Columns: product\n2. Filters: none\n3. Aggregations: SUM(amount) AS total_amount\n4. Grouping: product\n5. Ordering: total_amount DESC\n6. Limit: none",
 "sql": "SELECT product, SUM(amount) AS total_amount FROM sales GROUP BY product ORDER BY total_amount DESC"}
```
Cover different shapes: plain filter, aggregation + grouping, date bucketing, top-N with `LIMIT`,
and one `CANNOT_ANSWER` case (it teaches the refusal format).

### 17.2 Selection rule (the important design decision)
**Depends on:** 17.1.
- `select_examples(selected_columns, k=3)` in `app/prompts.py` keeps only examples whose
  `columns ⊆ selected_columns`.
- **Why:** the schema guard rests on the prompt never mentioning a column the user didn't select.
  An example using `quantity` while only `product, amount` are selected would invite the model to
  use `quantity`, and the Reviewer would then reject the SQL.
- Deterministic order (the order in the file, first k): the same input always gives the same
  prompt, so hashes are stable and eval runs are comparable.
- No matching examples → the examples block renders nothing (v3 behaves like v2).

### 17.3 v3 templates
**Depends on:** 17.2.
- `v3/planner_system.j2` extends v2 and overrides `{% block examples %}`:
  `{% for ex in examples %}<example>\n<request>{{ ex.request }}</request>\n<outline>\n{{ ex.outline }}\n</outline>\n</example>{% endfor %}`.
- `v3/coder_system.j2`: the same, with `outline → sql`.
- Examples go in the **system** prompt (trusted content), wrapped in `<example>` tags, and the
  injection-guard rule says examples are illustrations, not part of the current request.
- `PromptSet` passes `examples=select_examples(columns)` only for versions whose templates use it.
  Unused variables are harmless in Jinja, so passing them always keeps the code simple.

### 17.4 Example validation tests (examples are code)
- Every example's `sql` passes `app/reviewer.py` with that example's own `columns`.
- Every non-refusal `outline` has exactly the 6 numbered sections the Planner prompt requires.
- The refusal example starts with `CANNOT_ANSWER_PREFIX`.
- Every `columns` list is valid for `app/schema.py` (`validate_selection`).
- Selection never leaks: for **every** non-empty subset of the 4 columns (15 subsets), the
  rendered v3 prompt mentions no unselected column name.
- New golden files for v3 (all 4 columns).

## Pipeline impact

Quality gate only (new tests). The CI in-image check also renders the v3 templates and loads
`examples.json`. Production stays on v1.

## Verification

Gate green; CI green; after merge CD green. Optional: deploy v3 briefly (repo variable → re-run CD),
make one request, compare the `prompt.hash` and the token counts on the `llm.complete` span against v1, then switch back.
