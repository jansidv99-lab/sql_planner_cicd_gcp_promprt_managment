"""Prompt rendering for the Planner and Coder (Phase 14: Jinja2 templates; Phase 16: versions).

All prompt text lives in `app/prompt_templates/` as Jinja2 files; this module only renders them.
Each version is a folder (`v1/`, `v2/`, ...) with the same 4 templates; later versions extend
earlier ones (`{% extends %}`) and override blocks. A released version is never edited: changes
go into a new version, so `prompt.version` + `prompt.hash` always identify the exact text.

Rules:
- Only the trusted template files are templates. User text (the request) and model output (the
  outline) are passed in as *variables* and never compiled as templates, so `{{ ... }}` typed by a
  user stays literal text (no template injection).
- StrictUndefined: a missing variable raises instead of silently rendering as empty text.
- No autoescaping: prompts are not HTML, and the `<request>`/`<outline>` tags must stay as written.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app import config

CANNOT_ANSWER_PREFIX = "CANNOT_ANSWER:"

TEMPLATE_DIR = Path(__file__).parent / "prompt_templates"
EXAMPLES_FILE = TEMPLATE_DIR / "examples.json"
MAX_EXAMPLES = 3
_VERSION_DIR = re.compile(r"^v\d+$")


class PromptVersionError(ValueError):
    """PROMPT_VERSION names a version that has no template folder."""


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


def available_versions() -> tuple[str, ...]:
    """Version folders under prompt_templates/, in numeric order (v1, v2, ..., v10)."""
    names = [p.name for p in TEMPLATE_DIR.iterdir() if p.is_dir() and _VERSION_DIR.match(p.name)]
    return tuple(sorted(names, key=lambda n: int(n[1:])))


def render(version: str, name: str, **variables: object) -> str:
    """Render `<version>/<name>.j2` with the given variables."""
    return _env().get_template(f"{version}/{name}.j2").render(**variables)


def render_schema(table: str, columns: Sequence[object]) -> str:
    """Render the schema block (`_partials/schema.j2`) from already-validated Column objects."""
    return _env().get_template("_partials/schema.j2").render(table=table, columns=columns)


def prompt_hash(text: str) -> str:
    """Short fingerprint of a rendered prompt (sha256, 12 hex chars): safe to log, unlike the text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _types(columns: Iterable[str]) -> list[str]:
    """BigQuery types of the selected columns, sorted (what the v2+ hints depend on)."""
    from app.schema import column_types  # local import: schema imports this module lazily too

    return column_types(columns)


@dataclass(frozen=True)
class Example:
    """One worked example for few-shot prompts (Phase 17). `sql` is None for a refusal."""

    id: str
    columns: tuple[str, ...]
    request: str
    outline: str
    sql: str | None


@functools.lru_cache(maxsize=1)
def load_examples() -> tuple[Example, ...]:
    data = json.loads(EXAMPLES_FILE.read_text(encoding="utf-8"))
    return tuple(
        Example(d["id"], tuple(d["columns"]), d["request"], d["outline"], d.get("sql"))
        for d in data
    )


def select_examples(
    columns: Iterable[str], *, with_sql: bool = False, k: int = MAX_EXAMPLES
) -> list[Example]:
    """Examples whose columns are ALL selected, first k in file order (deterministic).

    The subset rule keeps the schema guard intact: a prompt never shows an example that uses a
    column the user didn't select. `with_sql=True` (Coder) skips refusal examples.
    """
    selected = set(columns)
    matching = [
        ex
        for ex in load_examples()
        if set(ex.columns) <= selected and (ex.sql is not None or not with_sql)
    ]
    return matching[:k]


@dataclass(frozen=True)
class PromptSet:
    """The 4 prompts of one version. System prompts get the selected columns so that versions
    can adapt to them (v2: type-aware hints, v3: few-shot examples); v1 ignores them."""

    version: str

    def planner_system(self, columns: Iterable[str]) -> str:
        columns = tuple(columns)
        return render(
            self.version,
            "planner_system",
            cannot_answer_prefix=CANNOT_ANSWER_PREFIX,
            types=_types(columns),
            examples=select_examples(columns),
        )

    def planner_user(self, schema_block: str, request: str) -> str:
        return render(self.version, "planner_user", schema_block=schema_block, request=request)

    def coder_system(self, columns: Iterable[str]) -> str:
        columns = tuple(columns)
        return render(
            self.version,
            "coder_system",
            types=_types(columns),
            examples=select_examples(columns, with_sql=True),
        )

    def coder_user(self, schema_block: str, outline: str) -> str:
        return render(self.version, "coder_user", schema_block=schema_block, outline=outline)


def get_prompts(version: str) -> PromptSet:
    if version not in available_versions():
        known = ", ".join(available_versions())
        raise PromptVersionError(f"Unknown prompt version {version!r} (available: {known})")
    return PromptSet(version)


def active_prompts() -> PromptSet:
    """The version this deployment uses (PROMPT_VERSION, default v1)."""
    return get_prompts(config.prompt_version())


def check_templates() -> list[str]:
    """Load every template and render every version once; used by CI inside the Docker image.

    Fails loudly if a template file is missing from the image or doesn't parse.
    """
    from app.schema import COLUMN_NAMES

    names = _env().list_templates(extensions=["j2"])
    for name in names:
        _env().get_template(name)
    for version in available_versions():
        p = get_prompts(version)
        p.planner_system(COLUMN_NAMES)
        p.coder_system(COLUMN_NAMES)
        p.planner_user("schema", "request")
        p.coder_user("schema", "outline")
    render_schema("table", [])
    print(f"templates ok: {len(names)} files, versions {', '.join(available_versions())}")
    return names
