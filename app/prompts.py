"""Prompt rendering for the Planner and Coder (Phase 14: Jinja2 templates).

All prompt text lives in `app/prompt_templates/` as Jinja2 files; this module only renders them.

Rules:
- Only the trusted template files are templates. User text (the request) and model output (the
  outline) are passed in as *variables* and never compiled as templates, so `{{ ... }}` typed by a
  user stays literal text (no template injection).
- StrictUndefined: a missing variable raises instead of silently rendering as empty text.
- No autoescaping: prompts are not HTML, and the `<request>`/`<outline>` tags must stay as written.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

CANNOT_ANSWER_PREFIX = "CANNOT_ANSWER:"

TEMPLATE_DIR = Path(__file__).parent / "prompt_templates"
VERSION = "v1"


def tag(text: str, name: str) -> str:
    """Jinja2 filter: wrap text in <name> ... </name> lines, e.g. `{{ request | tag("request") }}`."""
    return f"<{name}>\n{text}\n</{name}>"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["tag"] = tag
    return env


def render(name: str, **variables: object) -> str:
    """Render `<VERSION>/<name>.j2` with the given variables."""
    return _env().get_template(f"{VERSION}/{name}.j2").render(**variables)


def render_schema(table: str, columns: Sequence[object]) -> str:
    """Render the schema block (`_partials/schema.j2`) from already-validated Column objects."""
    return _env().get_template("_partials/schema.j2").render(table=table, columns=columns)


def planner_system() -> str:
    return render("planner_system", cannot_answer_prefix=CANNOT_ANSWER_PREFIX)


def planner_user_prompt(schema_block: str, request: str) -> str:
    return render("planner_user", schema_block=schema_block, request=request)


def coder_system() -> str:
    return render("coder_system")


def coder_user_prompt(schema_block: str, outline: str) -> str:
    return render("coder_user", schema_block=schema_block, outline=outline)


def check_templates() -> list[str]:
    """Load every template and render each prompt once; used by CI inside the Docker image.

    Fails loudly if a template file is missing from the image or doesn't parse.
    """
    names = _env().list_templates(extensions=["j2"])
    for name in names:
        _env().get_template(name)
    planner_system()
    coder_system()
    planner_user_prompt("schema", "request")
    coder_user_prompt("schema", "outline")
    render_schema("table", [])
    print(f"templates ok: {len(names)}")
    return names
