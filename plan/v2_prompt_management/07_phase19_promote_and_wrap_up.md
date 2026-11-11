# Phase 19 — Promote the best prompt version + wrap up (detailed plan)

**Status:** PLANNED, awaiting review.
**Part of:** [00_master_plan.md](00_master_plan.md) — prompt-engineering learning track
**Depends on:** Phase 18 (eval results for v1/v2/v3).
**Branch:** `chore/promote-prompt-version`

## Goal

Use the evaluation results to choose the production prompt, roll it out through the existing
CD pipeline with a clear rollback path, and write down what each technique actually changed.

## What you will learn

- Making a release decision from metrics, and stating the rule **before** looking at the numbers.
- A config-only rollout and rollback (no code change, no rebuild of the logic).
- Checking in production that the intended prompt is the one that actually ran (version + hash).

## Steps

### 19.1 Decide the rule first
Written into this doc before the full eval is run:
1. Highest **status accuracy**. A must-refuse case answered wrongly counts heavily against a version.
2. Then highest **check pass rate**.
3. A tie (within 1 case) → fewer prompt tokens wins (cheaper and faster).
4. A version that fails any injection case is disqualified.

### 19.2 Run the full eval and record the results
`python -m evals.run --versions v1 v2 v3 --repeat 2 --yes`. Paste the summary table into this doc,
plus the cases each version fails.

### 19.3 Roll out
1. `gh variable set PROMPT_VERSION --repo jansidv99-lab/sql_planner_cicd_gcp_promprt_managment --body <winner>`.
2. Re-run CD from the Actions tab (`workflow_dispatch`).
3. Verify: smoke check green; one UI request; the `chain finished` log shows
   `prompt_version: <winner>` and hashes that match a local render of the same templates.
- **Rollback:** set the variable back to `v1` and re-run CD. (Or use the Cloud Run traffic
  rollback to the previous revision; see `CLAUDE.md`.)

### 19.4 Learnings document
`plan/v2_prompt_management/08_prompt_engineering_learnings.md`: for each technique (templates,
partials, versioning, hints, few-shot, eval), what changed, the effect in numbers, and what you'd
do differently.

### 19.5 Update `CLAUDE.md`
- Template layout (`app/prompt_templates/vN/`, `_partials/`, `examples.json`) and the renderer in `app/prompts.py`.
- Rules: never render user input as a template; never edit a released version (add a new one);
  golden files change only together with a new version; never log prompt text (version and hash only).
- Commands: the eval command and its cost warning; how to switch `PROMPT_VERSION`.
- Master plan: mark Phases 14–19 done.

## Verification

CD green with the new variable; the production log and trace show the promoted version; the old
app (`sql-planner`) is still untouched (same before/after comparison as in Phase 13).
