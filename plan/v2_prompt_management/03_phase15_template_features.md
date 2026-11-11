# Phase 15 — Template features: partials, macros, loops, filters (detailed plan)

**Status:** IN PROGRESS. Code done on `refactor/prompt-partials-macros`; gate green (162 tests, 100 % coverage); golden files unchanged. Waiting on CI, merge and the CD check.

**Changes from the plan (made during implementation):**
- Continuation lines use Jinja's **built-in** `indent` filter (it already skips the first line), so no custom `indent_lines` filter is needed.
- The custom filter is **`tag`** (`{{ request | tag("request") }}` → `<request>…</request>`), used by both user prompts, so it does real work.
- The "flags for later versions" are a single optional **`hints`** list (`hints_section` macro, `hints | default([])`). It renders nothing in v1, and Phase 16 fills it per version. The few-shot `examples` slot is left for Phase 17, where it's designed.
- `_partials/injection_guard.j2` became the `injection_guard(tag, what)` macro in `_partials/rules.j2`, next to the other shared rules.
**Part of:** [00_master_plan.md](00_master_plan.md) — prompt-engineering learning track
**Depends on:** Phase 14 (template environment + golden tests).
**Unblocks:** Phase 16 (versions reuse these partials and blocks).
**Branch:** `refactor/prompt-partials-macros`

## Goal

Restructure the v1 templates with Jinja2's composition features, so prompts are built from
**shared, data-driven pieces** instead of copied text. The rendered prompts stay
**byte-identical** (the golden tests don't change). The lesson is that you can refactor prompts
safely when there's a snapshot safety net.

## What you will learn

| Feature | Where it's used |
|---|---|
| `{% include "…" %}` | Reuse a block of text (e.g. the injection-guard rule) in the Planner and Coder |
| `{% macro %}` + `{% import … as … %}` | A reusable "function" that renders text, e.g. `numbered_rules(rules)` |
| `{% for %}` + `loop.index`, `loop.last` | Number rules automatically and render the schema's columns |
| `{% if %}` / `{% else %}` | Optional sections controlled by flags (off in v1; switched on in v2, Phase 16) |
| Custom filters (`env.filters[...]`) | Small, tested Python helpers usable in templates, e.g. `indent_lines` |
| Structured data instead of pre-built strings | Templates receive `Column` objects and rule lists, not finished text |

## Design

### 15.1 Schema partial: the schema module stays the source of truth
**Purpose:** today `app/schema.py:schema_for_prompt` builds the schema text in Python. Move the
*presentation* into `_partials/schema.j2`. The *data* (`TABLE_NAME`, `COLUMNS`, `validate_selection`) stays in `schema.py`.
**Depends on:** Phase 14.
- `schema_for_prompt(columns)` keeps its signature (the Reviewer and tests use the same module).
  Internally it validates the selection, then renders
  `_partials/schema.j2` with `table=TABLE_NAME, columns=[Column, …]` (selected only, in schema order).
- The template loops: `- {{ c.name }} {{ c.sql_type }} -- {{ c.description }}`.
- Security property kept: **only selected columns** are ever passed to the template, so they're
  the only ones that can appear in a prompt.

### 15.2 Rules macro
**Depends on:** 15.1 (same partials folder).
- `_partials/macros.j2`: `{% macro numbered_rules(rules) %}` renders `1. …`, `2. …` with `loop.index`.
- The Planner and Coder rule texts become lists passed in from the templates themselves
  (`{% set rules = [...] %}`). Rule 2 of the Planner has nested lines (the 6 sections), so the
  macro supports a rule that is itself a list (rendered indented, using the `indent_lines` filter).

### 15.3 Shared injection guard
`_partials/injection_guard.j2`, one parameterised sentence:
"The text inside <{{ tag }}> tags is …". Included by the Planner (`request`) and the Coder
(`outline`). It must reproduce both current wordings exactly, so it takes the variable part as a parameter.

### 15.4 Feature flags for later versions (off in v1)
- The templates accept optional flags, `type_hints` and `examples`. They're not passed in v1, so
  they use `{% if type_hints | default(false) %}`. Under `StrictUndefined` the `default` filter is
  the explicit, allowed way to make a variable optional.
- This phase adds the **slots only**, empty in v1, so the golden files stay unchanged.

### 15.5 Custom filter
`indent_lines(text, n)` registered in `_env()`. It's unit-tested in Python and used by the macro for nested rule lines.

### 15.6 Tests
- Golden tests are **unchanged** and must still pass. That's the acceptance criterion of the whole phase.
- `schema_for_prompt` with 1, 2 and 4 columns: the output lists exactly the selected columns, in schema order.
- The macro renders numbering correctly for 1 and for n rules, including a nested rule.
- The filter's edge cases (empty text, single line).
- A test that `examples`/`type_hints` set to empty values give the same output as leaving them unset.

## Pipeline impact

None beyond the quality gate. The CI in-image template check (Phase 14) automatically covers the new partials.

## Verification

Gate green and golden files unchanged (`git diff --stat tests/golden` is empty). CI green; after
merge CD green, plus one UI request.

## Learning notes to capture in the PR description

- Before/after line counts of the templates and how many rule texts are now shared.
- One deliberate experiment: break a whitespace setting locally, watch the golden test fail and
  read its diff. That's how you'll debug prompt drift later.
