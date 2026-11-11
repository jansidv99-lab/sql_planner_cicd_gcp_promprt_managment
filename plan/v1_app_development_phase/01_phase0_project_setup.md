# Phase 0 — Project Setup (detailed plan)

**Status:** DRAFT, waiting for review and permission.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** nothing (first phase).
**Unblocks:** Phase 1 (config and schema) and everything after it.

## Goal

Create a clean, reproducible project base where:
- the secrets in `.env` can never be committed,
- anyone (you, CI, Docker) can install the same dependencies,
- the folder layout for the later phases exists.

## Current state (checked 2026-11-10)

- Folder has only `CLAUDE.md`, `.env`, `plan/`. Not a git repo.
- Tools installed: Python 3.11.9 (`python`), git 2.36, Docker 29.5, gcloud SDK, GitHub CLI.
- **Python version for the project: 3.11** (matches local; Docker and CI will use 3.11 too).

---

## Steps

### Step 0.1 — Initialize git and protect secrets
- **Why:** version control is needed for GitHub Actions; `.env` holds an API key and must be excluded **before** the first commit.
- **Actions:**
  1. `git init -b main`
  2. Create `.gitignore`:
     ```
     .env
     .venv/
     __pycache__/
     *.pyc
     .pytest_cache/
     .ruff_cache/
     ```
- **Depends on:** nothing.
- **Check:** `git status` doesn't list `.env`.

### Step 0.2 — Add `.env.example`
- **Why:** documents the required settings for anyone setting up the project, without exposing values.
- **Actions:** create `.env.example` with names only:
  ```
  OPENAI_API_KEY=
  OPENAI_BASE_URL=
  MODEL=
  GCP_PROJECT_ID=
  GCP_PROJECT_NUMBER=
  ```
- **Depends on:** 0.1 (so it's tracked while `.env` isn't).
- **Check:** file exists and contains no values.

### Step 0.3 — Create the virtual environment
- **Why:** keeps project packages separate from the system Python.
- **Actions:** `python -m venv .venv`, then activate it (`.venv\Scripts\Activate.ps1` in PowerShell).
- **Depends on:** 0.1 (`.venv/` is already ignored).
- **Check:** `python --version` inside the venv shows 3.11.x.

### Step 0.4 — Dependency files
- **Why:** pinned, repeatable installs for local, CI and Docker.
- **Actions:**
  - `requirements.txt` (runtime, used by Docker):
    | Package | Used for |
    |---|---|
    | `openai` | LLM calls (Planner, Coder) via the OpenAI-compatible endpoint |
    | `streamlit` | UI |
    | `sqlglot` | Reviewer: parsing BigQuery SQL |
    | `python-dotenv` | load `.env` when running locally |
  - `requirements-dev.txt` (local + CI): `-r requirements.txt`, `pytest`, `ruff`.
  - Pin exact versions (`package==x.y.z`) to the current releases at install time.
- **Depends on:** 0.3.
- **Check:** `pip install -r requirements-dev.txt` succeeds.

### Step 0.5 — Folder skeleton
- **Why:** gives each later phase a clear home.
- **Actions:** create empty placeholder files:
  ```
  app/__init__.py
  app/config.py      (Phase 1)
  app/schema.py      (Phase 1)
  app/llm.py         (Phase 2)
  app/prompts.py     (Phases 3–4)
  app/planner.py     (Phase 3)
  app/coder.py       (Phase 4)
  app/reviewer.py    (Phase 5)
  app/chain.py       (Phase 6)
  streamlit_app.py   (Phase 7)
  tests/__init__.py
  ```
  Each file gets a one-line docstring saying what it will contain. No logic yet.
- **Depends on:** 0.1.
- **Check:** `python -c "import app"` works.

### Step 0.6 — Tool settings (`pyproject.toml`)
- **Why:** one shared configuration for `ruff` and `pytest`, so local runs and CI behave the same.
- **Actions:** `pyproject.toml` with:
  - `[tool.ruff]` → `target-version = "py311"`, `line-length = 100`
  - `[tool.pytest.ini_options]` → `testpaths = ["tests"]`
- **Depends on:** 0.4.
- **Check:** `ruff check .` passes; `pytest` runs (0 tests collected is OK at this point).

### Step 0.7 — First commit
- **Why:** a clean starting point to build from.
- **Actions:** `git add .` → confirm `.env` is **not** staged → commit `"Phase 0: project setup"`.
- **Depends on:** 0.1–0.6.
- **Check:** `git log` shows the commit; `git ls-files` doesn't include `.env`.
- **Note:** creating the GitHub repository and pushing is left for Phase 10, with your approval then.

---

## Step dependencies

```
0.1 ─┬─► 0.2
     ├─► 0.3 ─► 0.4 ─► 0.6 ─┐
     └─► 0.5 ───────────────┴─► 0.7
```

## Files created in this phase

`.gitignore`, `.env.example`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `app/*.py` (empty placeholders), `streamlit_app.py` (placeholder), `tests/__init__.py`, plus `.venv/` (not committed).

## Out of scope

Any application logic, GitHub remote, Docker, or GCP setup; those come in later phases.
