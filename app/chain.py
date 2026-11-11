"""Run Planner -> Coder -> Reviewer once (no retries) and return one result object.

Expected failures come back as a Status + friendly message, with any partial results kept
(e.g. the outline when the Coder fails). Unexpected exceptions are bugs and propagate.

Phase 12: each run is one trace (`chain.run` with child spans `planner`, `coder`, `reviewer`)
and ends with one structured log line `chain finished`. Only metadata is recorded.

Phase 16: one prompt version per run (both steps use the same one). The version and a hash of
each rendered system prompt are recorded on the spans and in the log; never the prompt text.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum

from opentelemetry import trace

from app import config
from app.coder import CoderError, write_sql
from app.llm import LLMError, LLMFn, complete
from app.observability import mark_error, span
from app.planner import CannotAnswerError, PlannerError, make_plan
from app.prompts import PromptSet, PromptVersionError, get_prompts, prompt_hash
from app.reviewer import ReviewResult, review_sql
from app.schema import SchemaError, validate_selection

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)


class Status(str, Enum):
    OK = "ok"
    REVIEW_FAILED = "review_failed"
    CANNOT_ANSWER = "cannot_answer"
    INPUT_ERROR = "input_error"
    CODER_ERROR = "coder_error"
    LLM_ERROR = "llm_error"
    CONFIG_ERROR = "config_error"  # Phase 16: unknown PROMPT_VERSION


_LOG_LEVEL = {
    Status.OK: logging.INFO,
    Status.CANNOT_ANSWER: logging.INFO,
    Status.INPUT_ERROR: logging.INFO,
    Status.REVIEW_FAILED: logging.WARNING,
    Status.CODER_ERROR: logging.ERROR,
    Status.LLM_ERROR: logging.ERROR,
    Status.CONFIG_ERROR: logging.ERROR,
}


@dataclass(frozen=True)
class ChainResult:
    status: Status
    message: str | None  # friendly error text; None when ok
    columns: tuple[str, ...] = ()  # validated selection (empty on a bad selection)
    outline: str | None = None  # Planner output
    sql: str | None = None  # Coder output, as returned
    review: ReviewResult | None = None  # Reviewer verdict (errors + formatted SQL)
    prompt_version: str | None = None  # Phase 16: prompt version used (None if unknown)

    @property
    def passed(self) -> bool:
        return self.status is Status.OK


def run(
    request: str,
    selected_columns: Iterable[str],
    llm: LLMFn = complete,
    prompt_version: str | None = None,
) -> ChainResult:
    """Run the chain once. `prompt_version` overrides PROMPT_VERSION (used by the eval)."""
    request_chars = len((request or "").strip())
    durations: dict[str, int] = {}
    hashes: dict[str, str] = {}
    version = prompt_version or config.prompt_version()
    with span(_tracer, "chain.run", request_chars=request_chars) as root:
        root.set_attribute("prompt.version", version)
        try:
            result, error_type = _run_steps(
                request, selected_columns, llm, durations, version, hashes
            )
        except Exception as err:
            logger.exception(
                "chain crashed",
                extra={"json_fields": {"error_type": type(err).__name__, **_meta(request_chars)}},
            )
            raise
        root.set_attribute("status", result.status.value)
        root.set_attribute("columns", list(result.columns))
        if error_type:
            mark_error(root, error_type)

        # Logged while the `chain.run` span is still active, so the log entry carries this
        # run's trace ID and links to the trace in Cloud Logging.
        logger.log(
            _LOG_LEVEL[result.status],
            "chain finished",
            extra={
                "json_fields": {
                    "status": result.status.value,
                    "error_type": error_type,
                    "columns": list(result.columns),
                    "review_error_count": len(result.review.errors) if result.review else None,
                    "duration_ms": durations,
                    "prompt_version": version,
                    "prompt_hashes": hashes,
                    **_meta(request_chars),
                }
            },
        )
    return result


def _meta(request_chars: int) -> dict:
    return {"request_chars": request_chars}


def _run_steps(
    request: str,
    selected_columns: Iterable[str],
    llm: LLMFn,
    durations: dict[str, int],
    version: str,
    hashes: dict[str, str],
) -> tuple[ChainResult, str | None]:
    """The chain logic (Phase 6, plus one PromptSet per run since Phase 16).

    Returns the result and the error type, if any.
    """
    try:
        prompts = get_prompts(version)
    except PromptVersionError as err:
        return ChainResult(Status.CONFIG_ERROR, str(err)), type(err).__name__
    result, error_type = _run_with_prompts(
        request, selected_columns, llm, durations, prompts, hashes
    )
    return replace(result, prompt_version=prompts.version), error_type


def _run_with_prompts(
    request: str,
    selected_columns: Iterable[str],
    llm: LLMFn,
    durations: dict[str, int],
    prompts: PromptSet,
    hashes: dict[str, str],
) -> tuple[ChainResult, str | None]:
    # Validate the column selection first: no LLM call for a bad selection.
    try:
        columns = validate_selection(selected_columns)
    except SchemaError as err:
        return ChainResult(Status.INPUT_ERROR, str(err)), type(err).__name__

    # [1] Planner
    try:
        with span(_tracer, "planner", durations) as current:
            hashes["planner"] = prompt_hash(prompts.planner_system(columns))
            current.set_attribute("prompt.hash", hashes["planner"])
            outline = make_plan(request, columns, llm, prompts)
    except CannotAnswerError as err:  # subclass of PlannerError: check first
        return ChainResult(Status.CANNOT_ANSWER, str(err), columns), type(err).__name__
    except PlannerError as err:
        return ChainResult(Status.INPUT_ERROR, str(err), columns), type(err).__name__
    except LLMError as err:
        return ChainResult(Status.LLM_ERROR, str(err), columns), type(err).__name__

    # [2] Coder
    try:
        with span(_tracer, "coder", durations) as current:
            hashes["coder"] = prompt_hash(prompts.coder_system(columns))
            current.set_attribute("prompt.hash", hashes["coder"])
            sql = write_sql(outline, columns, llm, prompts)
    except CoderError as err:
        return ChainResult(Status.CODER_ERROR, str(err), columns, outline), type(err).__name__
    except LLMError as err:
        return ChainResult(Status.LLM_ERROR, str(err), columns, outline), type(err).__name__

    # [3] Reviewer (gate)
    with span(_tracer, "reviewer", durations) as current:
        review = review_sql(sql, columns)
        current.set_attribute("review.passed", review.passed)
        current.set_attribute("review.error_count", len(review.errors))
    if review.passed:
        return ChainResult(Status.OK, None, columns, outline, sql, review), None
    result = ChainResult(
        Status.REVIEW_FAILED,
        "The generated SQL failed the review checks",
        columns,
        outline,
        sql,
        review,
    )
    return result, "ReviewFailed"
