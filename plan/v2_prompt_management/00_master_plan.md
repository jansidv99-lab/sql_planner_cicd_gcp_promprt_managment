# SQL Script Generator v2 (prompt management) — Master Plan

**Status:** Phase 13 DONE (2026-11-11). Prompt-engineering track: Phases 14–16 ✅, Phases 17–19 planned. Live app: https://sql-planner-pm-309866353676.asia-south1.run.app

This folder holds **all planning for the new development** in the cloned repo
`jansidv99-lab/sql_planner_cicd_gcp_promprt_managment`. The baseline (Phases 0–12) is documented
in `plan/v1_app_development_phase/` (`00_master_plan.md` … `13_phase12_logging_tracing.md`). Those files are **kept as
history and not edited**. New phases continue the numbering from Phase 13.

| Phase | Plan doc | Result |
|---|---|---|
| 13 Separate GCP resources for this repo | `01_phase13_separate_gcp_resources.md` | ✅ PR #1 (+ docs PR #2); 141 tests, 100 % coverage; old app verified unchanged |
| 14 Jinja2 foundations | `02_phase14_jinja2_foundations.md` | ✅ PR #4; prompts byte-identical (golden tests), verified in production |
| 15 Template features (partials, macros, loops, filters) | `03_phase15_template_features.md` | ✅ PR #6; prompts byte-identical, verified in production |
| 16 Prompt versioning + observability | `04_phase16_versioning_observability.md` | ✅ PR #8; production on v1, version + hash verified in log and trace |
| 17 Few-shot examples | `05_phase17_few_shot_examples.md` | 📝 planned |
| 18 Evaluation harness (local only) | `06_phase18_evaluation_harness.md` | 📝 planned |
| 19 Promote best version + wrap up | `07_phase19_promote_and_wrap_up.md` | 📝 planned |

### Prompt-engineering learning track (Phases 14–19)

Learning goal: manage the Planner/Coder prompts as **Jinja2 templates** and improve them in
measured steps, using the existing quality gate → CI → CD pipeline. Each phase teaches one idea
and leaves production working.

```
14 templates (same text) ─► 15 partials/macros (same text) ─► 16 versions v1/v2 + version/hash in logs
                                                                 │
                                         17 few-shot v3 ◄────────┘
                                                 │
                          18 local eval v1 vs v2 vs v3 ─► 19 promote winner via PROMPT_VERSION
```

Decisions: templates in `app/prompt_templates/` (rendered by `app/prompts.py`); the version is
chosen per deploy with the `PROMPT_VERSION` repo variable → CD → Cloud Run; the eval runs **locally
only** (CI keeps needing no secrets); golden files keep v1 byte-identical through Phases 14–15.

---

## 1. Why v2 exists

This repo is a clone of `jansidv99-lab/sql_planner_cicd_gcp`, whose app is **live** on Cloud Run
as `sql-planner` (https://sql-planner-309866353676.asia-south1.run.app). The clone has the same
code, so it still hardcodes the old app's resource names in `scripts/gcp_setup.sh`,
`.github/workflows/cd.yml` and `app/observability.py`.

**Goal:** the new repo deploys a **separate** app, `sql-planner-pm`, with **its own** GCP
resources in the same GCP project. The old app and every resource it uses stay exactly as they are.

## 2. Resource mapping (old → new)

| Kind | Old (never modified) | New (this repo) |
|---|---|---|
| Cloud Run service | `sql-planner` | `sql-planner-pm` |
| Artifact Registry repo (`asia-south1`) | `sql-planner` | `sql-planner-pm` |
| Secret Manager secret | `sql-planner-openai-api-key` | `sql-planner-pm-openai-api-key` |
| Runtime service account | `sql-planner-runtime` | `sql-planner-pm-runtime` |
| Deployer service account | `sql-planner-deployer` | `sql-planner-pm-deployer` |
| Workload Identity pool | `github-pool` (belongs to a third app) | `sql-planner-pm-pool` (**new, dedicated**) |
| Workload Identity provider | `github-sql-planner` | `github-sql-planner-pm` |
| GitHub repo (repo variables + WIF condition) | `jansidv99-lab/sql_planner_cicd_gcp` | `jansidv99-lab/sql_planner_cicd_gcp_promprt_managment` |

Unchanged: GCP project (`GCP_PROJECT_ID` / `GCP_PROJECT_NUMBER` in `.env`) and region `asia-south1`.

## 3. Do-not-touch list

Never create, modify, bind or delete these:

- **Old app (`sql_planner_cicd_gcp`):** every item in the "Old" column above.
- **Third app (`chain_prompting_cloud_gcp`):** `gh-deployer`, `run-runtime`, `github-pool`,
  `github-provider`, Artifact Registry repo `apps`, secret `openai-api-key`, and its Cloud Run services.

## 4. Working conventions for v2

- Every phase: a plan doc in this folder → user review → permission → implementation.
- One branch per piece of work, never commit directly to `main`, open a PR, wait for CI,
  merge only after user approval. Ask before every commit.
- Branch names describe the work, with a type prefix:
  - `feature/<what>`: new capability (e.g. `feature/separate-gcp-resources`)
  - `fix/<what>`: bug fix (e.g. `fix/cd-smoke-check-403`)
  - `docs/<what>`: documentation only
  - `chore/<what>`: cleanup, dependencies, tooling
  - `refactor/<what>`: behaviour-preserving code changes
