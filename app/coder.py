"""Chain step 2: write one BigQuery SELECT statement from the Planner's outline."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.llm import LLMFn, complete
from app.prompts import coder_system, coder_user_prompt
from app.schema import schema_for_prompt

# First fenced block: ``` or ```sql (any language tag), content captured lazily.
_FENCE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)


class CoderError(RuntimeError):
    """The Coder could not produce any SQL text."""


def extract_sql(reply: str) -> str:
    """Return the SQL from the model reply, removing markdown fences and surrounding text.

    Does not judge the SQL itself; that is the Reviewer's job.
    """
    text = (reply or "").strip()
    match = _FENCE.search(text)
    if match:
        text = match.group(1)
    elif text.startswith("```"):  # opening fence with no closing fence
        text = text.split("\n", 1)[1] if "\n" in text else ""
    sql = text.strip()
    if not sql:
        raise CoderError("The model returned no SQL")
    return sql


def write_sql(outline: str, selected_columns: Iterable[str], llm: LLMFn = complete) -> str:
    """Return SQL for the outline. Called once: no retries."""
    outline = (outline or "").strip()
    if not outline:
        raise CoderError("No outline to write SQL from")
    schema_block = schema_for_prompt(selected_columns)

    reply = llm(coder_system(), coder_user_prompt(schema_block, outline))
    return extract_sql(reply)
