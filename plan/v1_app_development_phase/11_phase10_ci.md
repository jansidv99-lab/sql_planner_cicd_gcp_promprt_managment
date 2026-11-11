# Phase 10 — CI Pipeline with GitHub Actions (detailed plan)

**Status:** DONE (PR #1): both CI jobs green. Step 10.8 (branch protection) refused by GitHub: needs GitHub Pro for private repos, so it was skipped.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 8 (quality gate commands) ✅, Phase 9 (`Dockerfile`) ✅ written, not yet built locally.
**Unblocks:** Phase 11 (CD deploys only after CI passes).

## Goal

Every push and pull request is checked automatically on GitHub:
1. **Quality job:** lint, format check, tests with coverage (the Phase 8 gate, unchanged).
2. **Docker job:** build the image **and smoke-test it** (start the container, check the health endpoint). This also completes the Phase 9 verification that couldn't run locally.

CI needs **no secrets**: tests fake the LLM, and the container smoke test only checks that the app starts.

## Current state (checked 2026-11-10)

| Item | State | Impact |
|---|---|---|
| GitHub remote | **none**: the repo is local only | a GitHub repository must be created and the code pushed (Step 10.1); this publishes the code to GitHub |
| `gh` CLI | logged in as `jansidv99-lab`; token scopes `repo`, `gist`, `read:org` | **missing the `workflow` scope**: GitHub refuses pushes that add `.github/workflows/*` without it. **You** run `gh auth refresh -s workflow` once (opens a browser login) |
| Branch | everything committed directly on `main` | from this phase on: work on a branch → pull request → CI → merge |
| Action versions (latest releases) | `actions/checkout` v7, `actions/setup-python` v7, `docker/setup-buildx-action` v4, `docker/build-push-action` v7 | pinned to these major versions |

---

## Steps

### Step 10.1 — Create the GitHub repository and push `main`
- **Why:** GitHub Actions runs only on code hosted on GitHub.
- **What:**
  1. Confirm `.env` is not tracked (`git ls-files` check), so no secret is ever pushed.
  2. `gh repo create sql_planner_cicd_gcp --private --source . --remote origin --push` (pushes the existing `main`; **private** so only you can see it).
- **Depends on:** nothing. **Needs your approval (Decision 1):** this publishes the code to GitHub.

### Step 10.2 — Add the `workflow` scope to `gh`
- **Why:** required to push workflow files.
- **What:** **you** run `! gh auth refresh -s workflow` in this session (it opens a browser to confirm).
- **Depends on:** nothing; needed before 10.6.

### Step 10.3 — Feature branch
- **Why:** proves CI on a pull request (the "done when" for this phase) and starts the branch → PR → merge workflow CD will rely on.
- **What:** `git switch -c phase-10-ci`.
- **Depends on:** 10.1.

### Step 10.4 — Workflow file `.github/workflows/ci.yml`
- **Why:** defines what GitHub runs and when.
- **What:**
  ```yaml
  name: CI
  on:
    push:
      branches: [main]
    pull_request:
  permissions:
    contents: read                 # least privilege: CI only reads the code
  concurrency:
    group: ci-${{ github.ref }}
    cancel-in-progress: true       # a new push cancels the outdated run

  jobs:
    quality:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v7
        - uses: actions/setup-python@v7
          with:
            python-version: "3.11"
            cache: pip
            cache-dependency-path: requirements*.txt
        - run: pip install -r requirements-dev.txt
        - run: ruff check .
        - run: ruff format --check .
        - run: pytest --cov

    docker:
      needs: quality               # only build images from code that passed the gate
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v7
        - uses: docker/setup-buildx-action@v4
        - uses: docker/build-push-action@v7
          with:
            context: .
            load: true             # keep the image locally for the smoke test; no push
            tags: sql-planner:ci
            cache-from: type=gha
            cache-to: type=gha,mode=max
        - name: Smoke test container
          run: |
            docker run -d --name app -p 8080:8080 sql-planner:ci
            for i in $(seq 1 30); do
              curl -fsS http://localhost:8080/_stcore/health && exit 0
              sleep 2
            done
            docker logs app; exit 1
  ```
  | Part | Purpose |
  |---|---|
  | `on: push main + pull_request` | checks every PR and every merge to `main` |
  | `permissions: contents: read` | the workflow token can't modify the repo |
  | `concurrency` | saves CI minutes on rapid pushes |
  | pip cache | faster installs on repeat runs |
  | `needs: quality` | Docker build runs only after tests pass |
  | GitHub Actions layer cache (`type=gha`) | faster image rebuilds |
  | smoke test | proves the image starts and serves `/_stcore/health` (no API key needed: the app starts and shows "not configured") |
- **Depends on:** 10.3.

### Step 10.5 — Local checks before pushing
- **Why:** catch mistakes before using CI minutes.
- **What:** quality gate passes locally; YAML parses.
- **Depends on:** 10.4.

### Step 10.6 — Push the branch and open a pull request
- **Why:** run CI for real and see it pass.
- **What:** `git push -u origin phase-10-ci`, then `gh pr create` with a summary of the change. Watch the run (`gh pr checks --watch`).
- **Depends on:** 10.2, 10.5.

### Step 10.7 — Fix anything CI finds
- **Why:** first runs on a new platform often expose small environment differences (e.g. Linux paths, the Docker build itself, since it hasn't run locally).
- **What:** fix on the same branch, push again until both jobs are green. Each fix is reported to you.
- **Depends on:** 10.6.

### Step 10.8 — Require CI before merging (branch protection)
- **Why:** makes the gate enforceable: nothing reaches `main` (and, after Phase 11, production) without passing CI.
- **What:** protect `main`: require the `quality` and `docker` checks to pass and require a pull request (`gh api` call). **Only with your approval (Decision 3).**
- **Note:** branch protection on **private** repositories requires a paid GitHub plan (Pro/Team). On a free account this step will be refused; the alternative is to make the repo public or skip enforcement (CI still runs and reports).
- **Depends on:** 10.7.

### Step 10.9 — Review and merge
- **Why:** you stay in control of what lands on `main`.
- **What:** I show you the green PR; **you approve**; then merge (`gh pr merge --squash`) and update `CLAUDE.md` (CI section, branch workflow) in the same PR before merging.
- **Depends on:** 10.7 (10.8 optional).

---

## Step dependencies

```
10.1 (your approval) ─► 10.3 ─► 10.4 ─► 10.5 ─┐
10.2 (you run gh auth refresh) ───────────────┴─► 10.6 ─► 10.7 ─┬─► 10.8 (optional) ─┐
                                                                 └────────────────────┴─► 10.9 (your approval)
```

## Files changed in this phase

- `.github/workflows/ci.yml` (new)
- `CLAUDE.md` (CI and branch workflow)
- `plan/00_master_plan.md` (mark Phase 9 verified once the CI Docker job passes)

No application code changes. No secrets stored in GitHub for this phase.

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Create a GitHub repository and push the code? Name and visibility? | **Yes, `sql_planner_cicd_gcp`, private** |
| 2 | You run `gh auth refresh -s workflow` (browser login) so workflow files can be pushed? | **Yes**: required |
| 3 | Protect `main` so merges require green CI? (needs a paid plan for private repos) | **Yes if available**; otherwise skip and rely on the PR checks |
| 4 | From now on, work on branches with pull requests instead of committing to `main` directly? | **Yes**: needed for CI on PRs and for safe CD in Phase 11 |
