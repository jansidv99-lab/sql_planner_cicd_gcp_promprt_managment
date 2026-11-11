# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repo is a **clone** of `jansidv99-lab/sql_planner_cicd_gcp` (baseline Phases 0–12, live as
Cloud Run service `sql-planner`; plans in `plan/00_master_plan.md` … `plan/13_*.md`, kept as
history). New development ("v2", from Phase 13) deploys a **separate** app, `sql-planner-pm`,
with its own GCP resources. All new plan docs go in `plan/v2_prompt_management/` (status table:
`plan/v2_prompt_management/00_master_plan.md`); don't edit the baseline plan files.
Python 3.11, with dependencies pinned in `requirements*.txt`.

## Architecture

`streamlit_app.py` → `app/chain.py: run(request, columns)` → Planner → Coder → Reviewer, each once.

- `app/schema.py` is the single source of truth for the `sales` table; `validate_selection` and
  `schema_for_prompt` (only the selected columns) are used by every step.
- `app/planner.py` / `app/coder.py` call the LLM through an injected `llm: LLMFn` (default
  `app.llm.complete`); tests pass fakes, so no test hits the real API. All prompt text is in
  `app/prompts.py`. The Planner can stop early with `CANNOT_ANSWER:`.
- `app/reviewer.py` is the deterministic gate (sqlglot, BigQuery dialect, no LLM): one statement,
  read-only, only `sales` (or CTEs), no `*` except `COUNT(*)`, only selected columns or query aliases.
- `app/chain.py` turns expected errors into a `ChainResult.status` + message (keeping partial
  results); unexpected exceptions propagate. The UI renders by status and holds no logic.
- The openai SDK (3.x) uses `httpx2`, not `httpx`. UI tests use `streamlit.testing.v1.AppTest`
  and patch `app.chain.run` / `app.config.load_settings`.

## Commands

Run from the project root in PowerShell:

```powershell
.venv\Scripts\Activate.ps1                    # activate the venv
pip install -r requirements-dev.txt           # install runtime + dev deps
ruff check .                                  # lint
ruff format .                                 # auto-format (plan/ is excluded)
pytest                                        # all tests (LLM is faked; no API key needed)
pytest tests/test_schema.py::test_name        # a single test
streamlit run streamlit_app.py                # run the UI
python -m app.llm                             # one real LLM smoke call (costs a request)
```

Quality gate (CI runs exactly this; all must pass):

```powershell
ruff check .; ruff format --check .; pytest --cov   # coverage of app/ must be >= 90%
```

Docker (requires Docker Desktop running):

```powershell
docker build -t sql-planner:local .
docker run --rm -p 8080:8080 -v "${PWD}/.env:/app/.env:ro" sql-planner:local   # http://localhost:8080
```

`.env` values are quoted, so don't use `docker run --env-file .env` (it keeps the quotes in the
values); mount it read-only instead and let python-dotenv parse it. The image contains no secrets.

`tests/conftest.py` fails any test that builds the real OpenAI client: pass a fake `llm=`
(Planner/Coder/chain) or `client=` (`app.llm.complete`) instead.

## Git workflow and CI

- Never commit directly to `main`: work on a branch, open a PR (`gh pr create`), wait for CI, and
  merge only after the user approves.
- Repo: `jansidv99-lab/sql_planner_cicd_gcp_promprt_managment` (private). `.github/workflows/ci.yml` runs on PRs and
  pushes to `main`: job `quality` (the gate above) then job `docker` (build image + container
  smoke test on `/_stcore/health`). CI needs no secrets.
- `git push` authenticates via Git Credential Manager; the `gh` token has no `workflow` scope.
- Branch protection isn't available (private repo on a free plan), so "CI green before merge"
  is a convention, not enforced by GitHub.

## CD (Cloud Run)

- `.github/workflows/cd.yml` runs after CI succeeds on `main` (`workflow_run`) or manually
  (`workflow_dispatch`): keyless auth (WIF) → build → push to Artifact Registry (tag = commit SHA)
  → `gcloud run deploy sql-planner-pm` (region `asia-south1`) → health check. `workflow_run` only
  uses the workflow file from `main`, so CD changes can't be tested on a PR.
- One-time setup is `scripts/gcp_setup.sh` (re-runnable; needs a local `.env`). Dedicated
  resources: Artifact Registry repo `sql-planner-pm`, secret `sql-planner-pm-openai-api-key`, SAs
  `sql-planner-pm-runtime` / `sql-planner-pm-deployer`, WIF pool `sql-planner-pm-pool` with
  provider `github-sql-planner-pm` (only this repo's `main`). Config lives in GitHub repo variables.
- The same GCP project hosts other apps' resources; never modify them (the setup script's guard
  refuses these names): the **old app** (`sql-planner` service and AR repo,
  `sql-planner-openai-api-key`, `sql-planner-runtime`, `sql-planner-deployer`,
  `github-sql-planner`) and **chain_prompting_cloud_gcp** (`gh-deployer`, `run-runtime`,
  `github-pool`, `github-provider`, repo `apps`, secret `openai-api-key`).
- The service is public (`allUsers` → `roles/run.invoker`, set once by the owner). The deployer
  has `run.developer`, so CD can't change access. CD's smoke check relies on public access: on a
  private service it gets 403.
- Rollback: `gcloud run services update-traffic sql-planner-pm --region asia-south1
  --to-revisions <previous-revision>=100`, or re-run CD from the Actions tab.

## Logging and tracing (Phase 12)

- `app/observability.py`: `setup_logging()` attaches google-cloud-logging's `StructuredLogHandler`
  (JSON on stdout → Cloud Logging, auto-linked to the active trace; needs no credentials) to the
  `app` logger; `setup_tracing()` installs OpenTelemetry → Cloud Trace (every run sampled) only on
  Cloud Run (`K_SERVICE`, also used as the trace service name) or with `TRACING_ENABLED=true`. Both are called once from
  `streamlit_app.py` via `st.cache_resource`.
- Each chain run = trace `chain.run` → `planner`/`coder` (→ `llm.complete`) / `reviewer`, plus one
  log line `chain finished` (status, error type, columns, durations). Use `observability.span()`,
  which records only the error **type**: never put request text, prompts, outline or SQL in logs
  or span attributes (`tests/test_observability.py` checks this).
- Tests: `tests/conftest.py` installs an in-memory span exporter (`spans` fixture) and resets the
  `app` logger after each test.
- Log lines must be written while the relevant span is active, or they lose the trace link.
- To inspect production: `gcloud logging read 'resource.labels.service_name="sql-planner-pm" AND
  jsonPayload.message="chain finished"'`, then fetch the trace by ID
  (`GET cloudtrace.googleapis.com/v1/projects/<project>/traces/<id>`); the v1 list endpoint
  returned nothing during verification.

## Goal

Implement a "SQL script generator" as a prompt chain in Python:

1. **Planner:** generates the outline, a high-level plan.
2. **Coder:** generates the SQL code based on the outline.
3. **Reviewer (gate check):** deterministic check of the generated SQL (parses, plus the safety rules in
   Architecture), no retries.
4. **UI:** a Streamlit app where the user enters input and sees the output.
5. **CI/CD pipeline:** production-ready app with full CI and CD, deploying to GCP.
6. **Schema:** a fixed `sales` table with 4 columns: `order_date`, `quantity`, `product`, `amount`. The user may only select
   from these columns, and the schema is injected into the Planner and Coder prompts to prevent the
   LLM from hallucinating tables/columns.

## Decisions

- SQL dialect: BigQuery (used by the Coder prompt and the `sqlglot` parse check).
- CI/CD: GitHub Actions; hosting: Cloud Run.
- Code generation only: the app never executes SQL or touches data.

## Environment

Copy `.env.example` to `.env` and fill it in (`.env` holds secrets and is gitignored). It defines:
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL` — LLM access via an OpenAI-compatible endpoint; read the model name from `MODEL`, don't hardcode it.
- `GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER` — deployment target.

## Workflow rules

1. **Plan first:** for every step, write a detailed plan doc in `/plan` with clear descriptions of each topic.
2. **Review:** ask the user to review the plan doc.
3. **Permission:** always ask for permission before making any change to code.
4. **Phased implementation:** plan code phase by phase, step by step, stating the purpose of each step and its dependencies.
5. **Commit step:** always ask for permission before every commit
6. **branch naming:** always provide the meaning full descriptions for the branches use more profissional naming line featrures,bugs, fix .....
