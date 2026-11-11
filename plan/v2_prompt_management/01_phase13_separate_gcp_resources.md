# Phase 13 — Separate GCP resources for this repo (detailed plan)

**Status:** DONE (2026-11-11). Setup script run (all new resources created); PR #1 merged; first CD run deployed `sql-planner-pm-00001` with the expected 403 on the smoke check; service made public; manual CD re-run deployed `sql-planner-pm-00002-8gk`, all green. Verified in production: a UI request logged `chain finished` (status ok, no request text, labels `app=sql-planner-pm`), linked to trace `fbc55f48…` with spans chain.run → planner/coder (→ llm.complete) / reviewer. Old app compared before/after setup and after deploy: revision `sql-planner-00007-2c8` and all its IAM policies unchanged (only the new pool appears in the pool list). URL: https://sql-planner-pm-309866353676.asia-south1.run.app
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** baseline Phases 0–12 (code, CI, CD, logging), all inherited from the cloned repo.
**Unblocks:** every later v2 phase (each one needs a deploy target that is safe to change).
**Branch:** `feature/separate-gcp-resources`

## Goal

Merging to `main` in **this** repo deploys the app to a **new** Cloud Run service,
`sql-planner-pm`, using only **new** dedicated resources (names in
[00_master_plan.md §2](00_master_plan.md)). The old `sql-planner` service and its resources
are not changed in any way.

```
PR merged to main (new repo) ─► CI ─► CD ─► WIF pool sql-planner-pm-pool / provider github-sql-planner-pm
                                              ─► SA sql-planner-pm-deployer
                                              ─► push image to AR repo sql-planner-pm
                                              ─► deploy Cloud Run sql-planner-pm (runs as sql-planner-pm-runtime,
                                                 key from secret sql-planner-pm-openai-api-key)
```

---

## Current state (checked 2026-11-11, read-only)

| Item | State | Consequence |
|---|---|---|
| `scripts/gcp_setup.sh` | Hardcodes old names and `GITHUB_REPO=…/sql_planner_cicd_gcp` | **Running it now would add bindings to old resources.** Must change before anyone runs it. |
| `.github/workflows/cd.yml` | `SERVICE`/`AR_REPO`/`SECRET_NAME` = old names | Would deploy over the old service if the auth worked |
| New repo's GitHub variables | **none set** | CD on this repo currently fails at the auth step, so nothing is at risk today |
| `.env` in this clone | **missing** | Must be created (copy `.env.example` and fill in, or copy from the old repo) before step 13.6 |
| `app/observability.py:29` | `SERVICE_NAME = "sql-planner"` | Traces from the new app would show up under the old app's name |
| File `-v` at the repo root | Committed by mistake (pytest output from the old repo) | Remove |

---

## Steps

### 13.1 Rewrite `scripts/gcp_setup.sh` for the new resources
**Purpose:** make the one-time setup create only new resources and point at the new repo.
**Depends on:** nothing.
**Why edit in place instead of adding a copy:** keeping the old script in this clone would leave a
re-runnable command that changes old resources. Git history keeps the old version.

Changes:
1. Variables: `GITHUB_REPO="jansidv99-lab/sql_planner_cicd_gcp_promprt_managment"`,
   `AR_REPO="sql-planner-pm"`, `SECRET="sql-planner-pm-openai-api-key"`,
   SAs `sql-planner-pm-runtime` / `sql-planner-pm-deployer`, `POOL="sql-planner-pm-pool"`,
   `PROVIDER="github-sql-planner-pm"`. Update descriptions and display names to match.
2. **New step: create the WIF pool** if it's missing:
   `gcloud iam workload-identity-pools create sql-planner-pm-pool --location=global --display-name="sql-planner-pm GitHub"`,
   using the same `exists … || run …` skip pattern as the other steps.
3. Provider: same OIDC settings; condition
   `assertion.repository=='jansidv99-lab/sql_planner_cicd_gcp_promprt_managment' && assertion.ref=='refs/heads/main'`.
   The `workloadIdentityUser` principalSet uses the new pool.
4. **Safety guard** at the top: stop with an error if any of `AR_REPO`, `SECRET`, the SA names,
   `POOL` or `PROVIDER` matches a name on the do-not-touch list.
5. `gh variable set` targets the new repo (it already uses `$GITHUB_REPO`).

Roles stay the same as Phase 11/12: deployer = `run.developer` (project), `artifactregistry.writer`
(own repo only), `iam.serviceAccountUser` (runtime SA only); runtime = `secretAccessor` (own secret
only), `cloudtrace.agent`.

**Verify:** `bash -n scripts/gcp_setup.sh`; `grep` finds no old resource name outside the guard.

### 13.2 Point `.github/workflows/cd.yml` at the new service
**Purpose:** CD deploys to `sql-planner-pm`, never `sql-planner`.
**Depends on:** 13.1 (resource names).
- `SERVICE: sql-planner-pm`, `AR_REPO: sql-planner-pm`, `SECRET_NAME: sql-planner-pm-openai-api-key`.
- `--labels "app=sql-planner-pm,…"`; `concurrency.group: cd-production-pm`.
- `vars.*` (project, region, WIF provider, SAs, base URL, model) come from the **new** repo's
  variables, set by 13.1.
- Note: `workflow_run` uses the workflow file from `main`, so this change can only be tested after merge.

### 13.3 Trace service name follows the running service
**Purpose:** traces from the new app appear under `sql-planner-pm`.
**Depends on:** nothing.
- `app/observability.py`: `service_name()` returns `K_SERVICE` (Cloud Run sets it to the
  service name), else `DEFAULT_SERVICE_NAME = "sql-planner-pm"`. It's a function rather than a
  module constant so it's read when tracing is set up, not at import time (easier to test).
- `tests/test_observability.py`: update the `"sql-planner"` expectations; add one test where
  `K_SERVICE` is set to another name and check that the resource uses it.

**Verify:** `pytest tests/test_observability.py`, then the full gate with coverage ≥ 90%.

### 13.4 CI image tag (cosmetic)
`.github/workflows/ci.yml`: `sql-planner:ci` → `sql-planner-pm:ci`. It's a local tag on the runner
only, with no GCP effect.

### 13.5 Remove the stray file
`git rm -- -v`.

### 13.6 One-time GCP setup (run by you, as project owner)
**Depends on:** 13.1 merged (or run from the branch), `.env` created.
1. Create `.env` (`GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL`).
2. **Baseline snapshot of the old app** (for later comparison):
   `gcloud run services describe sql-planner --region asia-south1 --format="value(status.latestReadyRevisionName)"`,
   plus `gcloud secrets get-iam-policy sql-planner-openai-api-key` and
   `gcloud iam service-accounts get-iam-policy sql-planner-deployer@<project>.iam.gserviceaccount.com`.
3. `gh variable list --repo jansidv99-lab/sql_planner_cicd_gcp_promprt_managment` (expect empty).
4. Run `bash scripts/gcp_setup.sh` from Git Bash.

### 13.7 First deploy and public access
**Depends on:** 13.2–13.6.
1. Merge the PR, then CI → CD runs. Expect the **first smoke check to get 403**: a new service is
   private, and the deployer (`run.developer`) can't change access, by design (same as Phase 11).
2. Owner makes the service public:
   `gcloud run services add-iam-policy-binding sql-planner-pm --region asia-south1 --member=allUsers --role=roles/run.invoker`
3. Re-run CD from the Actions tab: the smoke check should pass.

### 13.8 Update `CLAUDE.md`
New repo name, new resource names, new URL, log query `resource.labels.service_name="sql-planner-pm"`,
rollback command for `sql-planner-pm`, the full do-not-touch list, and a pointer to
`plan/v2_prompt_management/`. Done in the same PR (the URL is filled in after 13.7, in a small `docs/` follow-up).

---

## Known trade-off

`roles/run.developer` is granted **at project level** (same as the old setup), so the new deployer
could technically deploy to other services in the project. A binding on the service itself is only
possible once the service exists. **Optional hardening after 13.7:** bind `run.developer` on
`sql-planner-pm` only and remove the project-level binding (a separate `chore/` PR, re-tested with a CD run).

## Verification (end to end)

1. Local gate: `ruff check .; ruff format --check .; pytest --cov` passes (coverage ≥ 90%).
2. CI green on the PR.
3. New resources exist: `gcloud artifacts repositories describe sql-planner-pm --location asia-south1`,
   `gcloud secrets describe sql-planner-pm-openai-api-key`,
   `gcloud iam workload-identity-pools providers describe github-sql-planner-pm --workload-identity-pool=sql-planner-pm-pool --location=global`.
4. CD green on the new repo; `curl <new URL>/_stcore/health` returns `ok`; the UI generates a query.
5. `gcloud logging read 'resource.labels.service_name="sql-planner-pm" AND jsonPayload.message="chain finished"'`
   returns the request with a trace ID.
6. **Old app unchanged:** repeat the 13.6 step 2 snapshot. The revision and IAM policies must be
   identical, and https://sql-planner-309866353676.asia-south1.run.app still answers.
