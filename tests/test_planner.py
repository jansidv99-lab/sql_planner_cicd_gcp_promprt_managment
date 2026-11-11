import pytest

from app.llm import LLMError
from app.planner import (
    MAX_REQUEST_CHARS,
    CannotAnswerError,
    PlannerError,
    make_plan,
)
from app.prompts import planner_system
from app.schema import SchemaError

OUTLINE = "1. Columns: product, amount\n2. Filters: none"


class FakeLLM:
    def __init__(self, reply=OUTLINE, error=None):
        self.calls = []
        self.reply = reply
        self.error = error

    def __call__(self, system, user):
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return self.reply


def test_returns_outline():
    assert make_plan("top products", ["product", "amount"], llm=FakeLLM()) == OUTLINE


def test_prompt_has_only_selected_columns():
    llm = FakeLLM()
    make_plan("top products", ["product", "amount"], llm=llm)
    ((_, user),) = llm.calls
    assert "product" in user and "amount" in user
    assert "order_date" not in user and "quantity" not in user


def test_request_wrapped_in_tags():
    llm = FakeLLM()
    make_plan("  top 5 products  ", ["product"], llm=llm)
    ((_, user),) = llm.calls
    assert "<request>\ntop 5 products\n</request>" in user


def test_uses_planner_system_prompt():
    llm = FakeLLM()
    make_plan("x", ["product"], llm=llm)
    assert llm.calls[0][0] == planner_system()


@pytest.mark.parametrize(
    "reply", ["CANNOT_ANSWER: no region column", "cannot_answer: no region column"]
)
def test_cannot_answer_raises_with_reason(reply):
    with pytest.raises(CannotAnswerError) as exc:
        make_plan("sales by region", ["amount"], llm=FakeLLM(reply=reply))
    assert exc.value.reason == "no region column"


@pytest.mark.parametrize("request_text", ["", "   ", None])
def test_empty_request_rejected_without_llm_call(request_text):
    llm = FakeLLM()
    with pytest.raises(PlannerError, match="Enter a request"):
        make_plan(request_text, ["amount"], llm=llm)
    assert llm.calls == []


def test_too_long_request_rejected_without_llm_call():
    llm = FakeLLM()
    with pytest.raises(PlannerError, match="too long"):
        make_plan("x" * (MAX_REQUEST_CHARS + 1), ["amount"], llm=llm)
    assert llm.calls == []


def test_max_length_request_accepted():
    make_plan("x" * MAX_REQUEST_CHARS, ["amount"], llm=FakeLLM())


def test_invalid_columns_rejected_without_llm_call():
    llm = FakeLLM()
    with pytest.raises(SchemaError):
        make_plan("sales by region", ["region"], llm=llm)
    assert llm.calls == []


def test_llm_error_passes_through():
    err = LLMError("LLM request timed out")
    with pytest.raises(LLMError) as exc:
        make_plan("x", ["amount"], llm=FakeLLM(error=err))
    assert exc.value is err
