# Phase 11 — CD Pipeline to Cloud Run (detailed plan)

**Status:** DONE. Setup script run and verified; PR #2 merged; first CD run deployed revision `sql-planner-00001` but its smoke check got 403 (service still private: the CD identity token isn't accepted); service made public (step 11.13) and a manual CD run deployed `sql-planner-00002` with the smoke check green. URL: https://sql-planner-309866353676.asia-south1.run.app
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 10 (CI on GitHub) ✅, Phase 9 (Docker image, verified in CI) ✅, Phase 1 config (reads `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL` from env vars) ✅.
**Unblocks:** Phase 12 (logging and tracing, verified on Cloud Run).

## Goal

Every merge to `main` that passes CI is **automatically deployed** to Cloud Run, with:
- **no long-lived keys anywhere** (GitHub proves its identity to GCP with short-lived tokens: Workload Identity Federation),
- the **API key only in Secret Manager**, injected at runtime,
- a **smoke check** after each deploy and a documented **rollback**.

```
PR merged to main ─► CI (ci.yml, Phase 10) ── passes ──► CD (cd.yml, Phase 11)
                                                           1. auth to GCP (WIF, no key)
                                                           2. build image, tag = commit SHA
                                                           3. push to Artifact Registry
                                                           4. gcloud run deploy
                                                           5. smoke check /_stcore/health
                     CI fails ──► CD never starts; production unchanged
```

---

## Current GCP state (checked 2026-11-10, read-only)

| Item | State |
|---|---|
| Project (from `.env` `GCP_PROJECT_ID`) | ACTIVE, billing enabled, project number matches `.env` |
| Your gcloud account | `roles/owner` on the project |
| APIs needed (Run, Artifact Registry, Secret Manager, IAM, IAM Credentials, STS) | **all already enabled** |
| Existing resources from your **other** repo (`chain_prompting_cloud_gcp`) | WIF pool `github-pool` with provider `github-provider` **restricted to that repo**; service accounts `gh-deployer` (project-wide `run.admin` + `artifactregistry.writer`) and `run-runtime`; secret `openai-api-key`; Artifact Registry repo `apps`; 3 Cloud Run services in `asia-south1` |

**Approach: don't modify anything that belongs to the other project.** This phase creates **dedicated** resources for this app (its own provider, service accounts, secret and image repository), reusing only the existing WIF **pool** as a container. The other project keeps working exactly as before.

---

## Steps

### Part A — One-time GCP setup (`scripts/gcp_setup.sh`)

A single, re-runnable script (skips anything that already exists) so the setup is documented and repeatable. Values come from `.env` (`GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`); nothing secret is written to the repo.

| Step | Resource | Why |
|---|---|---|
| 11.1 | Artifact Registry repo **`sql-planner`** (Docker, `asia-south1`) + cleanup policy **keep the latest 10 images** | stores the images CD builds; the cleanup policy keeps storage cost near zero |
| 11.2 | Secret **`sql-planner-openai-api-key`**, value read from your local `.env` and piped straight to Secret Manager (**never printed or saved anywhere else**) | the only place the API key lives in the cloud |
| 11.3 | Service account **`sql-planner-runtime`**, given `secretmanager.secretAccessor` **on that one secret only** | the identity the running app uses; it can read its own key and nothing else |
| 11.4 | Service account **`sql-planner-deployer`**, given: `run.developer` (deploy revisions; can't change who may access the service), `artifactregistry.writer` **on the `sql-planner` repo only**, `iam.serviceAccountUser` **on `sql-planner-runtime` only** | the identity GitHub Actions uses to deploy: least privilege |
| 11.5 | WIF provider **`github-sql-planner`** in the existing `github-pool`, condition: `repository == 'jansidv99-lab/sql_planner_cicd_gcp'` **and** `ref == 'refs/heads/main'`; deployer SA impersonable only by this repo | **only the `main` branch of this repo** can deploy; PR branches and other repos can't |
| 11.6 | GitHub **repository variables** (not secrets; none of these are sensitive): `GCP_PROJECT_ID`, `GCP_REGION`, `WIF_PROVIDER`, `DEPLOYER_SA`, `RUNTIME_SA`, `OPENAI_BASE_URL`, `MODEL`, set with `gh variable set` | the workflow reads configuration from here instead of hard-coding it in the repo |

- **Who runs it:** I run it with your approval (your account is owner); every command is printed first.
- **Depends on:** nothing.

### Part B — CD workflow (`.github/workflows/cd.yml`)

#### Step 11.7 — Trigger: only after CI passes on `main`
- **Why:** CI and CD stay separate files (clear split, as requested in Phase 10), and nothing deploys unless CI is green.
- **What:**
  ```yaml
  on:
    workflow_run:
      workflows: [CI]
      types: [completed]
      branches: [main]
    workflow_dispatch:          # manual redeploy / rollback button in the Actions tab
  jobs:
    deploy:
      if: github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success'
  concurrency:
    group: cd-production
    cancel-in-progress: false   # never interrupt a deploy halfway; queue the next one
  permissions:
    contents: read
    id-token: write             # lets GitHub issue the short-lived token for WIF
  ```
  It checks out **exactly the commit CI tested** (`github.event.workflow_run.head_sha`).

#### Step 11.8 — Authenticate (no keys)
- `google-github-actions/auth@v3` with `workload_identity_provider: ${{ vars.WIF_PROVIDER }}` and `service_account: ${{ vars.DEPLOYER_SA }}`; then `google-github-actions/setup-gcloud@v3`; then `gcloud auth configure-docker asia-south1-docker.pkg.dev`.

#### Step 11.9 — Build and push the image
- `docker build` from the same `Dockerfile` CI tested; tag `asia-south1-docker.pkg.dev/<project>/sql-planner/app:<commit SHA>`; `docker push`.
- **Why the commit SHA:** every deployed version is traceable to a commit, and old images stay available for rollback.

#### Step 11.10 — Deploy to Cloud Run
```
gcloud run deploy sql-planner \
  --image <image:SHA> --region asia-south1 \
  --service-account sql-planner-runtime@<project>.iam.gserviceaccount.com \
  --set-secrets OPENAI_API_KEY=sql-planner-openai-api-key:latest \
  --set-env-vars OPENAI_BASE_URL=<vars.OPENAI_BASE_URL>,MODEL=<vars.MODEL> \
  --port 8080 --cpu 1 --memory 1Gi \
  --min-instances 0 --max-instances 2 \
  --session-affinity --timeout 3600 \
  --startup-probe httpGet.path=/_stcore/health
```
| Flag | Why |
|---|---|
| `--service-account` runtime SA | the app runs as an identity that can only read its own secret |
| `--set-secrets` | key injected from Secret Manager at runtime; never in the image or GitHub |
| `--set-env-vars` | non-secret settings (Phase 1 config reads them) |
| `--memory 1Gi` | Streamlit + pandas + sqlglot need headroom beyond the 512 Mi default |
| `--min-instances 0` | scales to zero when unused, so costs nothing while idle |
| `--max-instances 2` | caps cost and LLM spend if the URL gets heavy traffic |
| `--session-affinity`, `--timeout 3600` | Streamlit keeps a WebSocket per browser session; affinity + long timeout keep sessions from dropping |
| `--startup-probe /_stcore/health` | Cloud Run only sends traffic once Streamlit is ready (the endpoint CI's smoke test uses) |

#### Step 11.11 — Smoke check after deploy
- `curl -fsS <service URL>/_stcore/health` must return `ok` (retries for ~60 s); otherwise the job fails and shows the Cloud Run logs link.
- Writes the service URL and image tag to the GitHub job summary.

#### Step 11.12 — Rollback (documented, not automatic)
- **Option 1:** Actions tab → CD → "Run workflow" (redeploys `main`).
- **Option 2 (instant):** route traffic back to the previous revision:
  `gcloud run services update-traffic sql-planner --region asia-south1 --to-revisions <previous-revision>=100`
- Documented in `CLAUDE.md`.

### Part C — Access, first deploy, verification

#### Step 11.13 — Who can open the app (Decision 2)
- `run.developer` (the deployer) **can't** make the service public: by design, that's a one-time owner decision.
- After the first deploy, **if you choose public access**, I run once (with your approval):
  `gcloud run services add-iam-policy-binding sql-planner --region asia-south1 --member=allUsers --role=roles/run.invoker`

#### Step 11.14 — Branch, PR, first deploy
- Branch `phase-11-cd` → PR with `scripts/gcp_setup.sh`, `cd.yml`, docs → CI green → **you approve** → merge.
- The merge to `main` runs CI, then CD **for the first time** → service created.
- **Note:** `workflow_run` workflows only run from the version on `main`, so CD can't be tested on the PR itself; the first real run is right after the merge. CI on the PR still checks everything else.

#### Step 11.15 — Verify end to end
1. CD run green; service URL in the job summary;
2. `/_stcore/health` returns `ok` on the public URL;
3. **you** open the URL and generate one query (2 real API calls; proves Secret Manager → env var → LLM works);
4. a PR branch can't deploy (WIF condition): confirmed by the provider condition, no extra test run needed;
5. update `CLAUDE.md` (CD section, rollback) and the master plan status in a small follow-up PR.

---

## Step dependencies

```
Part A (setup, your approval) 11.1 ─► 11.2 ─► 11.3 ─► 11.4 ─► 11.5 ─► 11.6 ─┐
Part B (cd.yml) 11.7 ─► 11.8 ─► 11.9 ─► 11.10 ─► 11.11 ; 11.12 docs ───────┤
                                                                           ▼
                        11.14 PR ─► CI green ─► your approval ─► merge ─► first CD run
                                                                           ▼
                                               11.13 public access (if chosen) ─► 11.15 verify
```

## Files changed in this phase

- `scripts/gcp_setup.sh` (new; one-time setup, re-runnable)
- `.github/workflows/cd.yml` (new)
- `CLAUDE.md` (CD, rollback, GCP resource names)

No application code changes. **Nothing in the other project's GCP resources is modified.**

## Cost notes

- Cloud Run with `min-instances 0`: no cost while idle; free tier covers light use.
- Artifact Registry: a few hundred MB, capped at 10 images by the cleanup policy.
- Secret Manager: one secret, within the free tier.
- The main variable cost is **LLM calls** (2 per "Generate SQL" click); `max-instances 2` limits abuse, but a public URL means anyone with the link can trigger calls.

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Create **dedicated** GCP resources for this app (new provider, 2 service accounts, secret, image repo) instead of reusing/modifying the other project's? | **Yes, dedicated**: the other project stays untouched; least privilege per app |
| 2 | App access: **public** (anyone with the URL; needed for a demo/course submission) or **private** (only Google accounts you authorize, opened via `gcloud run services proxy`)? | **Public**, with `max-instances 2` to cap cost; can be switched to private later with one command |
| 3 | Region **`asia-south1`** (same as your other services)? | **Yes** |
| 4 | Store the API key as a new secret **copied from your local `.env`** (value never printed)? | **Yes** |
| 5 | I run `scripts/gcp_setup.sh` with your approval (it creates GCP resources and sets GitHub repo variables)? | **Yes** |
