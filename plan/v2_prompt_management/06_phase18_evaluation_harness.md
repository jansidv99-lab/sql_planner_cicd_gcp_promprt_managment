# Phase 18 — Evaluation harness, local only (detailed plan)

**Status:** IN PROGRESS. Code done on `feature/prompt-eval-harness`; gate green (297 tests, 100 % coverage of `app/`); dry run prints the plan (18 cases x 3 versions = up to 108 calls) and exits 2 without `--yes`. Next: the local smoke run, then the full run (Phase 19 uses its table).

**Implementation notes:**
- Dataset: 18 cases (12 answer cases, 4 must-refuse, 2 injection attempts that expect `cannot_answer` or a safe `ok`; a `review_failed` counts as a miss because the model was manipulated and only the gate saved it).
- Checks: `group_by` (follows aliases and `GROUP BY 1` back to the SELECT list), `agg:FUNC(col)` (also inside `ROUND(...)`), `agg:COUNT(*)`, `func:NAME`, `filter`, `order`, `limit`, all on the sqlglot AST in the Reviewer's dialect. Property checks are scored only for accepted (`ok`) SQL; on any other status they count as failed.
- The runner reads tokens and latency from the app's own spans via an in-memory exporter (tests pass the conftest exporter), so `app/` needed no change.
- `evals/` is excluded from the Docker image (`.dockerignore`); `evals/results/` is gitignored. Coverage is still measured on `app/` only, but `evals/` has its own unit tests (fake LLM).
**Part of:** [00_master_plan.md](00_master_plan.md) — prompt-engineering learning track
**Depends on:** Phase 16 (`chain.run(prompt_version=…)`, versions) and Phase 17 (v3).
**Unblocks:** Phase 19 (pick the version to promote using the results).
**Branch:** `feature/prompt-eval-harness`

## Goal

Measure prompt versions **with numbers** instead of impressions: run a fixed dataset of requests
through the real chain for each version and score the results deterministically. Decision made:
the eval runs **locally only** with your `.env`. CI only runs its unit tests with a fake LLM, and
**still needs no secrets**.

## What you will learn

| Concept | Where |
|---|---|
| Golden dataset | Fixed inputs plus expected properties, versioned in git next to the prompts |
| Deterministic scoring | Use what the app already has: the Reviewer verdict, the chain status, and sqlglot AST checks. No LLM-as-judge. |
| Property checks, not exact SQL | Many SQL texts are correct, so check properties ("groups by product", "uses SUM(amount)") |
| Variance | `temperature=0` reduces randomness but doesn't remove it, so `--repeat N` shows how stable each version is |
| Cost control | Count the planned calls up front and require explicit confirmation |
| Reusing observability | Token counts and latency come from the existing `llm.complete` spans (in-memory exporter), so `llm.py` doesn't change |

## Design

### 18.1 Dataset: `evals/dataset.json` (~15–20 cases)
```json
{"id": "top3_products_by_revenue",
 "columns": ["product", "amount"],
 "request": "Top 3 products by total revenue",
 "expect": {"status": "ok",
            "checks": ["group_by:product", "agg:SUM(amount)", "order:desc", "limit:3"]}}
```
Mix: easy cases, date bucketing (`order_date`), filters, top-N, ambiguous wording, **must-refuse**
cases (e.g. asking for `customer` → `cannot_answer`), and prompt-injection attempts in the request
(expected: still a safe query, or a refusal).

### 18.2 Scoring: `evals/scoring.py` (pure functions, unit-tested)
- `score_case(case, result: ChainResult) -> CaseScore`:
  - `status_ok`: does `result.status` match `expect.status`?
  - `review_ok`: did the Reviewer pass (for expected-`ok` cases)?
  - `checks`: each check is evaluated on the sqlglot AST of `result.review` / `result.sql`
    (BigQuery dialect, the same as `app/reviewer.py`): `group_by:<col>`, `agg:<FUNC>(<col>)`,
    `order:asc|desc`, `limit:<n>`, `filter:<col>`.
- `summarise(scores) -> per-version metrics`: status accuracy, Reviewer pass rate, check pass rate,
  mean prompt/completion tokens, p50/p95 latency, and the number of `llm_error` results.

### 18.3 Runner: `evals/run.py`
`python -m evals.run --versions v1 v2 v3 [--cases id1,id2] [--repeat 1] --yes`
1. Load the dataset; compute the planned calls (`cases × versions × repeat × 2`) and print them. Exit unless `--yes`.
2. Install an OpenTelemetry `InMemorySpanExporter` (the same approach as `tests/conftest.py`) to read
   `llm.prompt_tokens`, `llm.completion_tokens` and the span durations per run.
3. For each case × version: `chain.run(case.request, case.columns, prompt_version=v)` with the real
   `app.llm.complete`. Run sequentially, with no parallelism, to stay inside rate limits.
4. Output:
   - A markdown table on stdout (one row per version, plus a per-case pass/fail grid).
   - Full JSON in `evals/results/<timestamp>.json`. `evals/results/` is **gitignored**, and it
     holds generated SQL but never the API key.
- `evals/` is outside `app/`: it's development tooling, so `.dockerignore` gets `evals/` and it's
  never shipped in the image. Coverage stays measured on `app/` only, as today.

### 18.4 Tests (run in CI, fake LLM only)
- `tests/test_eval_scoring.py`: each check type passes and fails on hand-written SQL; status mismatch; summary maths.
- `tests/test_eval_runner.py`: the runner with a fake `llm` (the runner accepts `llm=` like the
  chain does): it refuses to run without `--yes`, counts calls correctly, and writes the JSON.
  `tests/conftest.py` already blocks the real client, so a mistake can't spend money in CI.
- Dataset validity: every case has valid columns and known check names.

## Pipeline impact

| Stage | Change |
|---|---|
| Quality gate / CI | New unit tests; ruff now also lints `evals/`. No secrets, no real LLM calls. |
| Docker image | Unchanged (`evals/` excluded) |
| CD | None |

## Verification

1. Gate green; CI green.
2. Locally: `python -m evals.run --versions v1 --cases <2 ids> --yes` (about 4 real calls) as a smoke run.
3. Full run: `python -m evals.run --versions v1 v2 v3 --yes` (≈ 18 cases × 3 versions × 2 ≈ 108 calls;
   the exact count is printed first). Save the table for Phase 19.
