# Phase 14 — Jinja2 foundations (detailed plan)

**Status:** IN PROGRESS. Code done on `feature/jinja2-prompt-templates`: 4 v1 templates, renderer, golden files captured from the f-strings before the change, CI in-image check. Gate green (152 tests, 100 % coverage); waiting on CI, merge and the CD check.
**Part of:** [00_master_plan.md](00_master_plan.md) — prompt-engineering learning track (Phases 14–19)
**Depends on:** Phase 13 ✅ (separate deploy target, so experiments never touch the old app).
**Unblocks:** Phase 15 (partials and macros need the template environment).
**Branch:** `feature/jinja2-prompt-templates`

## Goal

Move the Planner and Coder prompts out of Python f-strings into **Jinja2 template files**, with
**exactly the same rendered text**. The app's behaviour in production doesn't change at all. What
changes is where prompts live and how they're built, which every later phase relies on.

## What you will learn

| Concept | What it means here |
|---|---|
| `Environment` + loader | One configured object that finds templates (`FileSystemLoader` on `app/prompt_templates/`) and holds the rendering options |
| Delimiters | `{{ expr }}` prints a value, `{% stmt %}` is logic, `{# … #}` is a comment that never reaches the LLM |
| Whitespace control | `trim_blocks` (drop the newline after a `{% %}` tag), `lstrip_blocks` (drop indentation before a tag), `{%-` / `-%}` (trim by hand). Prompts are whitespace-sensitive, so this matters. |
| `keep_trailing_newline` | Off by default: Jinja drops **one** trailing newline, so a file that ends with `\n` renders like today's f-strings (which have none) |
| `StrictUndefined` | A missing or misspelled variable **raises** `UndefinedError` instead of silently rendering as empty text. That's essential for prompts: a silently missing schema block would let the LLM invent columns. |
| `autoescape=False` | Autoescaping is for HTML. For LLM text it would turn `<request>` into `&lt;request&gt;` and break the tag-based injection guard. |
| **Template injection** | Only **trusted files** are templates. User text (request, outline) is passed **as a variable** and never compiled as a template, so `{{ 7*7 }}` in a request stays literal text. Never call `Template(user_text)` or `env.from_string(user_text)`. |

## Current state

- `app/prompts.py`: `CANNOT_ANSWER_PREFIX`, `PLANNER_SYSTEM` (an f-string that uses the prefix),
  `planner_user_prompt(schema_block, request)`, `CODER_SYSTEM`, `coder_user_prompt(schema_block, outline)`.
- Callers: `app/planner.py:make_plan`, `app/coder.py:write_sql`.
- Tests that reference the constants: `tests/test_planner.py:51`, `tests/test_coder.py:64-70`.
- Jinja2 3.1.6 is already installed, but only as a dependency of altair/pydeck, so nothing pins it.

## Target layout

```
app/
  prompts.py                    # renderer: environment + small public functions
  prompt_templates/
    v1/
      planner_system.j2
      planner_user.j2
      coder_system.j2
      coder_user.j2
tests/
  golden/v1/                    # today's exact rendered prompts (fixed inputs)
    planner_system.txt  planner_user.txt  coder_system.txt  coder_user.txt
  test_prompts.py
```

Templates sit under `app/`, so the Dockerfile's `COPY app/ app/` packages them with no Dockerfile change.

## Steps

### 14.1 Capture golden files first (before touching the prompts)
**Purpose:** a byte-exact safety net. If any later step changes a single character of a prompt, a test fails.
**Depends on:** nothing.
- A one-off script (not committed) renders today's 4 prompts from fixed inputs into `tests/golden/v1/*.txt`:
  all 4 columns selected, request `"Total amount per product, highest first"`, and a sample 6-section outline.
- Commit the golden files first (in the same PR), so the diff shows they were taken from the f-string version.

### 14.2 Pin the dependency
`requirements.txt`: add `jinja2==3.1.6` (already installed, so the image size and lockstep stay the same).
Code that imports a package directly should pin it, instead of relying on a dependency of a dependency.

### 14.3 Write the templates
**Depends on:** 14.1.
- Copy each prompt's text into its `.j2` file. Interpolations become variables:
  `{{ cannot_answer_prefix }}`, `{{ schema_block }}`, `{{ request }}`, `{{ outline }}`.
- Add a `{# … #}` header comment to each file saying what it is for and which variables it needs.
  Comments don't reach the LLM.

### 14.4 Rewrite `app/prompts.py` as the renderer
**Depends on:** 14.2, 14.3.
- `_env()`, cached with `functools.lru_cache`:
  `Environment(loader=FileSystemLoader(Path(__file__).parent / "prompt_templates"), undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)`.
- `render(name: str, **variables) -> str`.
- Public functions, so callers stay simple: `planner_system()`, `planner_user_prompt(schema_block, request)`,
  `coder_system()`, `coder_user_prompt(schema_block, outline)`. `CANNOT_ANSWER_PREFIX` stays a Python
  constant, because `planner.py` also parses replies with it. It's passed into the template as a variable.
- Remove the `PLANNER_SYSTEM` / `CODER_SYSTEM` constants and update the two tests to call the functions.
  Phase 16 will need functions anyway (different versions).

### 14.5 Update callers
`planner.py` / `coder.py`: `llm(planner_system(), planner_user_prompt(...))`. That's a one-line change in each.

### 14.6 Tests (`tests/test_prompts.py`)
- **Golden**: each of the 4 renders, with the 14.1 inputs, equals its golden file byte for byte.
- **All templates parse**: `env.list_templates()` → `get_template()` for each.
- **StrictUndefined**: rendering `planner_user.j2` without `request` raises `UndefinedError`.
- **No template injection**: a request of `"{{ 7*7 }} {% if true %}x{% endif %}"` appears literally in the output, and `49` doesn't.
- **Injection guard intact**: `<request>` and `</request>` appear unescaped.

### 14.7 Pipeline: prove the templates are in the image
**Depends on:** 14.4.
`ci.yml` docker job, after the build and before the smoke test, adds one step:
`docker run --rm sql-planner-pm:ci python -c "from app.prompts import check_templates; check_templates()"`,
where `check_templates()` loads and test-renders every template. If a `.j2` file were ever excluded
from the image (for example by `.dockerignore`), CI fails here instead of production failing on the first request.
CD unchanged.

## Pipeline impact

| Stage | Change |
|---|---|
| Quality gate | New tests; coverage stays ≥ 90 % (`prompts.py` fully covered) |
| CI docker job | +1 step: render all templates inside the built image |
| CD | None. The image just contains `app/prompt_templates/`. |
| Production behaviour | Identical prompts (guaranteed by the golden tests) |

## Verification

1. Gate: `ruff check .; ruff format --check .; pytest --cov`.
2. CI green on the PR, including the new in-image template step.
3. After merge: CD green, then one UI request on https://sql-planner-pm-309866353676.asia-south1.run.app
   gives the same kind of result as before; `chain finished` has status `ok`.

## Risks

| Risk | Mitigation |
|---|---|
| Whitespace drift (an extra or missing newline changes the prompt) | Golden tests compare bytes; `trim_blocks`/`lstrip_blocks` are set once in the environment |
| Windows CRLF in `.j2` files | `.gitattributes` already forces `eol=lf`; the golden comparison would catch `\r` |
| Someone later renders user text as a template | Test 14.6 plus a rule in `CLAUDE.md` (added in Phase 19) |
