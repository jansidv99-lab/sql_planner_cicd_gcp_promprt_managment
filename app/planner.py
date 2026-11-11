"""Chain step 1: turn the user request into a 6-section plain-text outline."""

from __future__ import annotations

from collections.abc import Iterable

from app.llm import LLMFn, complete
from app.prompts import CANNOT_ANSWER_PREFIX, planner_system, planner_user_prompt
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


def make_plan(request: str, selected_columns: Iterable[str], llm: LLMFn = complete) -> str:
    """Return the outline text. Input is validated before any LLM call."""
    request = validate_request(request)
    schema_block = schema_for_prompt(selected_columns)

    reply = llm(planner_system(), planner_user_prompt(schema_block, request)).strip()

    if reply.upper().startswith(CANNOT_ANSWER_PREFIX):
        raise CannotAnswerError(reply[len(CANNOT_ANSWER_PREFIX) :].strip() or "no reason given")
    return reply
