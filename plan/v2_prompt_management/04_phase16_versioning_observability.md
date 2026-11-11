# Phase 16 — Prompt versioning + observability (detailed plan)

**Status:** DONE (2026-11-11). PR #8 (squash `c312c3b`). Gate: 197 tests, 100 % coverage; CI in-image check: 10 files, versions v1, v2. CD deployed `sql-planner-pm-00009-2gk` with `PROMPT_VERSION=v1` (repo variable unset → default). A UI request logged `prompt_version: v1`, `prompt_hashes` planner `7ffeb84c20a3` / coder `34e4b75928dd`, the same on the spans (trace `e417d529…`), and **identical to a local render of v1** for the same columns (v2 would be `fcaacb41dcd1` / `259c878b7426`).

**Implementation notes (changes from the plan):**
- Hints are computed **in the v2 template** from `types` (the sorted BigQuery types of the selected columns, from the new `schema.column_types()`). Every version gets `types`; v1 ignores it. The v1 `hints` slot from Phase 15 became `{% block hints %}`, the extension point v2 overrides.
- The system prompts now depend on the selection (v2), so `prompt.hash` identifies the exact text **for that selection**.
- An unknown version is checked in **one** place, `prompts.get_prompts()`. `config.prompt_version()` only reads the value. The chain turns `PromptVersionError` into `Status.CONFIG_ERROR` (no LLM calls), and the UI shows it as an error.
- `ChainResult.prompt_version` carries the version to the UI caption.
- `tests/conftest.py` pins `PROMPT_VERSION=v1`, so a local `.env` can't change test results.
- `gcp_setup.sh` sets `PROMPT_VERSION=v1` **only if it's missing**, so re-running it never resets a promoted version. `cd.yml` falls back to `v1` when the variable is unset.
- Extra test: for all 15 column selections, no v1/v2 prompt names an unselected column. It caught an alias example (`total_amount`) that mentioned `amount`, now `total_value`.
**Part of:** [00_master_plan.md](00_master_plan.md) — prompt-engineering learning track
**Depends on:** Phase 15 (partials and flag slots).
**Unblocks:** Phase 17 (v3 extends v2), Phase 18 (the eval compares versions).
**Branch:** `feature/prompt-versioning`

## Goal

Support several **prompt versions** side by side. The deployed app picks one with
`PROMPT_VERSION` (an env var per deploy), and every run records **which** version and exact prompt
produced it, without logging prompt text. This phase also adds the first prompt that is actually
better: **v2**, with type-aware hints.

## What you will learn

| Concept | Where |
|---|---|
| Template inheritance (`{% extends %}`, `{% block %}`, `{{ super() }}`) | `v2/*.j2` extends `v1/*.j2` and overrides only what changes |
| Prompts as versioned artifacts | Versions are folders. You never edit a released version: you add a new one. |
| Prompt fingerprinting | `prompt.hash` (sha256 of the rendered system prompt) tells you exactly which text ran, even when only a partial changed |
| Config by environment | `PROMPT_VERSION` flows from a GitHub repo variable → CD → Cloud Run → the app |
| Observability of prompts | Version and hash on spans and in logs, so production results can be compared by version |

## Design

### 16.1 Blocks in v1 (no output change)
Wrap the logical sections of each v1 template in named blocks (`role`, `rules`, `hints`,
`examples`, `output_format`). `hints` and `examples` are empty. The golden tests must still pass.

### 16.2 v2 = v1 + type-aware hints
**Depends on:** 16.1.
- `v2/planner_system.j2`: `{% extends "v1/planner_system.j2" %}` and overrides `{% block hints %}`.
- Hints are conditional on the **selected** column types (passed as `types`, e.g. `{"DATE", "NUMERIC"}`):
  - DATE selected → "For periods, use `DATE_TRUNC(order_date, MONTH)` or `EXTRACT(YEAR FROM order_date)`."
  - NUMERIC selected → "Round money results with `ROUND(x, 2)`."
  - INT64 or NUMERIC with aggregation → "Give every aggregate an alias."
- The same idea for `v2/coder_system.j2` (BigQuery function hints).
- New golden files: `tests/golden/v2/…` (rendered with all columns), plus a test that hints for
  unselected types are **absent**.

### 16.3 `PromptSet`: version as a value, not a global
**Depends on:** 16.1.
- `app/prompts.py`: `@dataclass(frozen=True) class PromptSet: version: str`, with methods
  `planner_system(types)`, `planner_user(schema_block, request)`, `coder_system(types)`,
  `coder_user(...)`, and `system_hash(text) -> str` (sha256, first 12 hex characters).
- `available_versions()` lists the `vN/` folders; `get_prompts(version)` raises `PromptVersionError` for an unknown version.
- `app/config.py`: `prompt_version(env=None) -> str` reads `PROMPT_VERSION` (default `"v1"`) and
  validates it with `available_versions()`. It's deliberately separate from `load_settings()`, so
  reading the version never needs the API key, and tests and the eval can call it freely.

### 16.4 Plumb it through the chain
**Depends on:** 16.3.
- `make_plan(..., prompts: PromptSet | None = None)` and `write_sql(..., prompts=None)`: if `None`,
  use `get_prompts(prompt_version())`.
- `chain.run(request, columns, llm=complete, prompt_version: str | None = None)` resolves one
  `PromptSet` per run and passes it to both steps, so the Planner and Coder always use the
  **same** version. An invalid version becomes a `ChainResult` error status (a new `config_error`
  status, logged at ERROR), not a crash.
- The UI (`streamlit_app.py`) shows `Prompt version: v2` in a small caption, using the existing
  render-by-status pattern. No logic goes in the UI.

### 16.5 Observability
**Depends on:** 16.4.
- Span attributes: `chain.run` gets `prompt.version`; `planner` and `coder` get `prompt.hash`
  (hash of their own system prompt).
- The `chain finished` log gains `prompt_version` and `prompt_hashes: {planner, coder}`.
- Privacy: extend the existing check in `tests/test_observability.py` so that no span attribute or
  log field contains any line of the rendered prompts. Only the version and hashes are allowed.

### 16.6 Pipelines
- `cd.yml` deploy step: add `PROMPT_VERSION=${{ vars.PROMPT_VERSION || 'v1' }}` to `--set-env-vars`.
  `workflow_run` uses the workflow from `main`, so this takes effect after merge.
- `scripts/gcp_setup.sh` step 11.6: `setvar PROMPT_VERSION v1`. The setup script is re-runnable;
  or set it once with `gh variable set PROMPT_VERSION --body v1`.
- Switching versions in production = change the repo variable, then re-run CD (Actions tab).
  Rollback = set it back and re-run. **Production stays on v1 in this phase** (v2 is promoted in
  Phase 19, after it has been measured).

### 16.7 Tests
- Golden v1 unchanged; new golden v2.
- `prompt_version()`: default, valid, invalid → error.
- `chain.run(prompt_version="v2")` passes v2 prompts to the fake LLM for **both** steps.
- Invalid version → `Status.CONFIG_ERROR` with a friendly message.
- Hash: stable for the same text and different when one character changes.

## Verification

1. Gate green; CI green.
2. After merge, with `PROMPT_VERSION` unset or `v1`: CD green, then a UI request; the log shows
   `prompt_version: "v1"` and hashes; the trace shows `prompt.version`.
3. Optional learning check: deploy v2 by setting the variable and re-running CD, make the same
   request, compare the hashes and the SQL, then set it back to v1.
