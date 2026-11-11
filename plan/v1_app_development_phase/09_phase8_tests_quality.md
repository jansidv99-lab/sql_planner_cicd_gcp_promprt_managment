# Phase 8 — Tests and Code Quality (detailed plan)

**Status:** DRAFT, waiting for review and permission.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phases 1–7 ✅ (116 tests, `ruff check` clean).
**Unblocks:** Phase 10 (CI runs exactly these commands), Phase 9 (consistent line endings for the Docker build).

## Goal

Most of the test suite was built alongside each phase. This phase turns it into a **quality gate CI can enforce**: the same commands, the same results, on any machine, with **no API key**.

## Current state (checked 2026-11-10)

| Item | State |
|---|---|
| Tests | 116 passing; LLM always faked; UI tested with `AppTest` |
| `ruff check` | ✅ passes (ruff 0.17 default rule set) |
| `ruff format --check` | ❌ **10 files would be reformatted** (layout only, e.g. long call arguments split over lines) |
| Coverage measurement | not set up |
| Line endings | git converts files to Windows CRLF on this machine (`core.autocrlf=true`); Linux CI/Docker expect LF |
| "No API key" guarantee | true in practice, but not proven by a test run |

---

## Steps

### Step 8.1 — Consistent line endings (`.gitattributes`)
- **Why:** removes the CRLF warnings seen on every commit, and makes sure files in the repo (and in the Docker image built on Linux) use LF regardless of whose machine committed them.
- **What:** `.gitattributes` with `* text=auto eol=lf`, then `git add --renormalize .` so already-committed files are normalized once.
- **Depends on:** nothing.
- **Check:** no "LF will be replaced by CRLF" warnings on the next commit.

### Step 8.2 — Code formatting with `ruff format`
- **Why:** one automatic code style means no style debates and clean diffs; CI will reject unformatted code.
- **What:**
  - `[tool.ruff.format]` in `pyproject.toml` (defaults: double quotes, line length 100 from the existing setting).
  - Run `ruff format .` once: reformats the 10 files. **Layout only: no logic changes**; the full test suite is rerun to prove it.
- **Depends on:** 8.1 (so the reformat commit has normalized line endings).
- **Check:** `ruff format --check .` passes.

### Step 8.3 — Coverage measurement and minimum
- **Why:** shows which code the tests exercise, and stops future changes from quietly dropping tests.
- **What:**
  - Add `pytest-cov` to `requirements-dev.txt` (pinned).
  - `pyproject.toml`: `[tool.coverage.run] source = ["app"]` and `[tool.coverage.report] fail_under = 90, show_missing = true`. Lines that only run as a manual smoke check (`if __name__ == "__main__":` in `app/llm.py`) are excluded.
  - Command: `pytest --cov` (prints a per-file table with missed lines).
  - `streamlit_app.py` is covered by the `AppTest` tests but `AppTest` runs it in a way coverage can't always track, so the minimum applies to `app/` (the logic).
- **Depends on:** nothing.
- **Check:** `pytest --cov` passes and reports ≥ 90 % for `app/`.

### Step 8.4 — Prove tests need no API key
- **Why:** CI must never need the real key, and a test that accidentally calls the real API would be slow, flaky and cost money.
- **What:**
  - An autouse fixture in `tests/conftest.py` that **blocks real LLM access**: it replaces `app.llm.get_client` with a function that fails the test immediately ("tests must not call the real LLM"). Tests that need an LLM already pass fakes, so nothing else changes.
  - Run the suite once with the LLM variables set to empty values (as CI will have) to confirm.
- **Depends on:** nothing.
- **Check:** full suite passes with `OPENAI_API_KEY=""`, `OPENAI_BASE_URL=""`, `MODEL=""`.

### Step 8.5 — One "quality gate" command list
- **Why:** you, CI (Phase 10) and future contributors run exactly the same checks.
- **What:** the gate is these three commands, in this order:
  ```
  ruff check .
  ruff format --check .
  pytest --cov
  ```
  Recorded in `CLAUDE.md` (Commands section) and used as-is by CI in Phase 10.
- **Depends on:** 8.2, 8.3, 8.4.

### Step 8.6 — Verify and commit
- Run the full gate (8.5).
- Two commits, so the layout-only changes are easy to review separately:
  1. `"Phase 8: normalize line endings and apply ruff format"` (8.1 + 8.2: no logic changes)
  2. `"Phase 8: coverage gate and no-real-LLM guard"` (8.3 + 8.4 + 8.5)
- **Depends on:** 8.1–8.5.

---

## Step dependencies

```
8.1 ─► 8.2 ─┐
8.3 ────────┼─► 8.5 ─► 8.6
8.4 ────────┘
```

## Files changed in this phase

- `.gitattributes` (new)
- `pyproject.toml` (format + coverage settings)
- `requirements-dev.txt` (+ `pytest-cov`)
- `tests/conftest.py` (new: no-real-LLM guard)
- 10 Python files reformatted (layout only)
- `CLAUDE.md` (quality gate commands)

No real API calls in this phase.

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Apply `ruff format` to the whole codebase now (layout-only changes to 10 files)? | **Yes**, in its own commit |
| 2 | Coverage minimum for `app/`? | **90 %** |
| 3 | Add `.gitattributes` to force LF line endings in the repo? | **Yes** |
