# Phase 9 — Containerize (detailed plan)

**Status:** DONE. Verified in CI (PR #1, Phase 10): image builds, container serves `/_stcore/health` (`ok`) and runs as `appuser`. Local Docker Desktop checks (9.5–9.6) were skipped.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 7 (Streamlit app) ✅, Phase 8 (LF line endings, quality gate) ✅.
**Unblocks:** Phase 10 (CI builds this image), Phase 11 (Cloud Run runs this image).

## Goal

A small, secure Docker image that runs the Streamlit app the same way locally, in CI and on Cloud Run, with **no secrets inside the image**.

## Current state (checked 2026-11-10)

| Item | State | Impact |
|---|---|---|
| Docker CLI | 29.5.2 installed | ✅ |
| Docker daemon | **not running** (Docker Desktop is closed) | **You'll need to start Docker Desktop** before the local build in Step 9.5 |
| `.env` values | wrapped in double quotes (`KEY="value"`) | `docker run --env-file .env` would pass the quotes **as part of the values** (e.g. a broken API key), so the local run uses a different method (Step 9.5) |
| Cloud Run | sets a `PORT` env var (default 8080); the app must listen on it at `0.0.0.0` | handled in the start command |

---

## Steps

### Step 9.1 — `.dockerignore`
- **Why:** keeps secrets and unneeded files out of the build context and image; smaller, faster builds.
- **What:** exclude `.env`, `.env.*` (but keep nothing secret), `.venv/`, `.git/`, `tests/`, `plan/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.coverage`, `*.md`, `.github/`.
- **Depends on:** nothing.
- **Check (9.6):** `.env` is not inside the built image.

### Step 9.2 — `Dockerfile`
- **Why:** one reproducible recipe for the runtime environment.
- **What:**
  ```dockerfile
  FROM python:3.11-slim

  ENV PYTHONDONTWRITEBYTECODE=1 \
      PYTHONUNBUFFERED=1 \
      PIP_NO_CACHE_DIR=1 \
      PIP_DISABLE_PIP_VERSION_CHECK=1 \
      PORT=8080

  WORKDIR /app

  # 1. dependencies first: this layer is cached until requirements.txt changes
  COPY requirements.txt .
  RUN pip install -r requirements.txt

  # 2. application code only
  COPY app/ app/
  COPY streamlit_app.py .

  # 3. run as a non-root user
  RUN useradd --create-home --uid 10001 appuser
  USER appuser

  EXPOSE 8080
  CMD ["sh", "-c", "exec streamlit run streamlit_app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
  ```
  | Line | Purpose |
  |---|---|
  | `python:3.11-slim` | same Python minor version as local and CI; slim = small image |
  | `PYTHONUNBUFFERED=1` | logs appear immediately (needed for Cloud Logging later) |
  | requirements before code | code changes don't re-install packages (faster builds) |
  | only runtime `requirements.txt` | no pytest/ruff in the production image |
  | non-root `appuser` | limits damage if the app were ever compromised |
  | `${PORT}` via `sh -c` + `exec` | listens on Cloud Run's port; `exec` lets Streamlit receive stop signals for a clean shutdown |
  | `--server.headless=true` | no "open browser" prompt; server mode |
- **No secrets:** the image has no API key; it's supplied at runtime (locally: Step 9.5; Cloud Run: Secret Manager in Phase 11). Without it the app shows the friendly "App is not configured" message from Phase 7.
- **Depends on:** 9.1.

### Step 9.3 — No Docker `HEALTHCHECK`
- **Why:** Cloud Run ignores Docker's `HEALTHCHECK`; it uses its own startup/liveness probes. Streamlit already serves a health endpoint at `/_stcore/health`, which Phase 11 will configure as the Cloud Run probe.
- **What:** nothing added; documented here so it isn't mistaken for an oversight.

### Step 9.4 — Document the commands
- **Why:** you, CI and future contributors build/run the image the same way.
- **What:** add the build/run commands (below) to `CLAUDE.md`.
- **Depends on:** 9.2, 9.5.

### Step 9.5 — Local build and run (needs Docker Desktop running)
- **Why:** prove the image builds and the app works inside it before CI/CD depend on it.
- **What:**
  ```powershell
  docker build -t sql-planner:local .
  docker run --rm -p 8080:8080 -v "${PWD}/.env:/app/.env:ro" sql-planner:local
  ```
  - **How secrets get in locally:** `.env` is **mounted read-only** at runtime (not copied into the image), and the app's existing `python-dotenv` loading reads it, quotes handled correctly, exactly as it does outside Docker. This avoids the `--env-file` quoting problem and doesn't touch your `.env`.
- **Depends on:** 9.2; **you start Docker Desktop**.

### Step 9.6 — Verify the image
- **Why:** confirm the security and runtime properties, not just "it builds".
- **Checks:**
  1. `http://localhost:8080/_stcore/health` returns `ok`; the page returns HTTP 200;
  2. `.env`, `tests/`, `plan/` are **not** in the image (`docker run --rm sql-planner:local ls -a /app`);
  3. the container runs as `appuser`, not root (`whoami`);
  4. without the `.env` mount, the app still starts (health `ok`) and would show "App is not configured";
  5. image size reported (expected roughly 400–600 MB, mostly Streamlit and its dependencies);
  6. **you** open `http://localhost:8080` and generate one query (2 real API calls).
- **Depends on:** 9.5.

### Step 9.7 — Verify and commit
- Quality gate still passes (`ruff check .`, `ruff format --check .`, `pytest --cov`).
- Commit `"Phase 9: Dockerfile"`.
- **Depends on:** 9.4, 9.6.

---

## Step dependencies

```
9.1 ─► 9.2 ─► 9.5 (Docker Desktop running) ─► 9.6 ─┐
       9.3 (decision only)                          ├─► 9.7
       9.4 (docs) ──────────────────────────────────┘
```

## Files changed in this phase

- `Dockerfile` (new)
- `.dockerignore` (new)
- `CLAUDE.md` (Docker commands)

No changes to application code.

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Base image tag: `python:3.11-slim` (gets security patches automatically on rebuild) or pinned `python:3.11.9-slim` (exactly matches local)? | **`python:3.11-slim`**: patch updates are safe and pick up security fixes; tests in CI catch any issue |
| 2 | Local secrets: mount `.env` read-only into the container (recommended), or remove the quotes from `.env` so `--env-file` works? | **Mount read-only**: no change to your `.env` |
| 3 | You start Docker Desktop for the local build and checks (9.5–9.6)? | **Yes**: needed to verify the image before CI/CD |
