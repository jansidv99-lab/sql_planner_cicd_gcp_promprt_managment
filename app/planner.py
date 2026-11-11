"""Chain step 1: turn the user request into a 6-section plain-text outline."""

from __future__ import annotations

from collections.abc import Iterable

from app.llm import LLMFn, complete
from app.prompts import CANNOT_ANSWER_PREFIX, PromptSet, active_prompts
from app.schema import schema_for_prompt

MAX_REQUEST_CHARS = 1000


class PlannerError(ValueError):
    """The request is not valid (empty or too long)."""


class CannotAnswerError(PlannerError):
    """The selected columns cannot answer the request; `reason` comes from the model."""

    def __init__(self, reason: str):
        super().__init__(f"Can't answer with the selected columns: {reason}")
        self.reason = reason


def validate_request(request: str) -> str:
    request = (request or "").strip()
    if not request:
        raise PlannerError("Enter a request")
    if len(request) > MAX_REQUEST_CHARS:
        raise PlannerError(f"Request is too long (max {MAX_REQUEST_CHARS} characters)")
    return request


def make_plan(
    request: str,
    selected_columns: Iterable[str],
    llm: LLMFn = complete,
    prompts: PromptSet | None = None,
) -> str:
    """Return the outline text. Input is validated before any LLM call.

    `prompts` picks the prompt version (default: this deployment's PROMPT_VERSION).
    """
    request = validate_request(request)
    selected_columns = tuple(selected_columns)
    schema_block = schema_for_prompt(selected_columns)
    prompts = prompts or active_prompts()

    system = prompts.planner_system(selected_columns)
    reply = llm(system, prompts.planner_user(schema_block, request)).strip()

    if reply.upper().startswith(CANNOT_ANSWER_PREFIX):
        raise CannotAnswerError(reply[len(CANNOT_ANSWER_PREFIX) :].strip() or "no reason given")
    return reply
