# Phase 18b — v4: explicit "refuse, don't substitute" rule (mini-phase)

**Status:** IN PROGRESS.
**Part of:** [00_master_plan.md](00_master_plan.md) — prompt-engineering learning track (an experiment added after Phase 18's results)
**Depends on:** Phase 18 (eval harness, stored v1/v2/v3 results).
**Unblocks:** Phase 19 (promote the winner of v2 vs v4).
**Branch:** `feature/prompt-v4-explicit-refusal` (stacked on PR #11)

## Why

The Phase 18 eval (3 runs per version) found one weakness shared by v1 and v2. When the request needs a
column that isn't selected (`refuse_unselected_column`: "Total revenue per product" with only `product`),
they **answer a different question** (`SELECT product FROM sales GROUP BY product`): v1 0/3, v2 1/3.
The Reviewer can't catch this, because the SQL is valid. v3's refusal example fixed it (3/3), but it also
made v3 refuse a valid request (`order_count_per_product` 0/3).

**Hypothesis:** one explicit sentence fixes the substitution without the over-refusal that an example caused.

## What you will learn

- An **instruction against an example** for the same goal: which generalises better, and at what token cost.
- `{{ super() }}`: extending a parent block's output instead of copying its logic.
- Measuring **one change at a time**: v4 differs from v2 by exactly one sentence.

## Design

- `v4/planner_system.j2` extends v2. Its `hints` block keeps v2's hints (`{% set base = super() %}`) and
  always adds one hint:
  "If answering needs information that none of the listed columns holds, reply with the
  `CANNOT_ANSWER:` line. Never answer a different, easier question instead (for example returning only
  names when totals were asked for)." The wording names no schema column, so the leak test still holds.
- The Coder and user prompts: inherited from v2 unchanged (refusing is the Planner's job).
- Tests: golden v4; v4 = v2 + exactly that hint, for every selection (also when v2 has no hints);
  the existing "no unselected column" test covers v4 automatically.

## Measure

`python -m evals.run --versions v4 --repeat 2 --yes`: up to 72 calls. Compare with the stored v2 runs
(same dataset, model and day) on status accuracy, check pass rate and tokens, and specifically on
`refuse_unselected_column` (did the substitution stop?) and `order_count_per_product` /
`ambiguous_best_products` (did it start over-refusing?).

## Decision (feeds Phase 19)

Phase 19's rule, applied to v2 against v4: status accuracy first, then check pass rate, then tokens; any injection failure disqualifies a version.
