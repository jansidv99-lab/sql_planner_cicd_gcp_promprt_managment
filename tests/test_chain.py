import pytest

from app.chain import Status, run
from app.llm import LLMError

COLS = ["product", "amount"]
OUTLINE = (
    "1. Columns: product\n2. Filters: none\n3. Aggregations: SUM(amount) AS total_sales\n"
    "4. Grouping: product\n5. Ordering: total_sales DESC\n6. Limit: 5"
)
GOOD_SQL = (
    "SELECT product, SUM(amount) AS total_sales FROM sales "
    "GROUP BY product ORDER BY total_sales DESC LIMIT 5"
)


class ScriptedLLM:
    """Returns (or raises) the scripted replies in order: 1st = Planner, 2nd = Coder."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def test_ok_path():
    llm = ScriptedLLM(OUTLINE, f"```sql\n{GOOD_SQL}\n```")
    result = run("top 5 products by total sales", COLS, llm=llm)
    assert result.status is Status.OK and result.passed
    assert result.message is None
    assert result.columns == ("product", "amount")
    assert result.outline == OUTLINE
    assert result.sql == GOOD_SQL
    assert result.review.passed and "\n" in result.review.formatted_sql
    assert len(llm.calls) == 2


def test_review_failed_keeps_outline_and_sql():
    bad_sql = "SELECT product, quantity FROM sales"
    result = run("products and quantities", COLS, llm=ScriptedLLM(OUTLINE, bad_sql))
    assert result.status is Status.REVIEW_FAILED and not result.passed
    assert result.outline == OUTLINE and result.sql == bad_sql
    assert "Column not selected: quantity" in result.review.errors


def test_cannot_answer_stops_after_planner():
    llm = ScriptedLLM("CANNOT_ANSWER: no date column selected")
    result = run("monthly sales trend", COLS, llm=llm)
    assert result.status is Status.CANNOT_ANSWER
    assert "no date column selected" in result.message
    assert result.sql is None and result.review is None
    assert len(llm.calls) == 1


@pytest.mark.parametrize("request_text", ["", "   "])
def test_empty_request_is_input_error_without_llm(request_text):
    llm = ScriptedLLM()
    result = run(request_text, COLS, llm=llm)
    assert result.status is Status.INPUT_ERROR
    assert "Enter a request" in result.message
    assert llm.calls == []


def test_bad_columns_is_input_error_without_llm():
    llm = ScriptedLLM()
    result = run("sales by region", ["region"], llm=llm)
    assert result.status is Status.INPUT_ERROR
    assert "region" in result.message
    assert result.columns == ()
    assert llm.calls == []


def test_coder_error_keeps_outline():
    result = run("top products", COLS, llm=ScriptedLLM(OUTLINE, "```sql\n```"))
    assert result.status is Status.CODER_ERROR
    assert result.outline == OUTLINE
    assert result.sql is None and result.review is None


def test_llm_error_in_planner():
    llm = ScriptedLLM(LLMError("LLM request timed out"))
    result = run("top products", COLS, llm=llm)
    assert result.status is Status.LLM_ERROR
    assert result.message == "LLM request timed out"
    assert result.outline is None
    assert len(llm.calls) == 1


def test_llm_error_in_coder_keeps_outline():
    result = run("top products", COLS, llm=ScriptedLLM(OUTLINE, LLMError("LLM rate limit reached")))
    assert result.status is Status.LLM_ERROR
    assert result.outline == OUTLINE and result.sql is None


def test_unexpected_exception_propagates():
    with pytest.raises(ZeroDivisionError):
        run("top products", COLS, llm=ScriptedLLM(ZeroDivisionError()))


def test_status_values_are_strings():
    assert Status.OK == "ok" and Status.REVIEW_FAILED.value == "review_failed"


# --- Phase 16: prompt versions --------------------------------------------------------------


def test_default_run_uses_v1_for_both_steps():
    from app.prompts import get_prompts

    llm = ScriptedLLM(OUTLINE, GOOD_SQL)
    result = run("top 5 products by total sales", COLS, llm=llm)
    v1 = get_prompts("v1")
    assert result.prompt_version == "v1"
    assert llm.calls[0][0] == v1.planner_system(COLS)
    assert llm.calls[1][0] == v1.coder_system(COLS)


def test_prompt_version_argument_overrides_env_for_both_steps():
    from app.prompts import get_prompts

    llm = ScriptedLLM(OUTLINE, GOOD_SQL)
    result = run("top 5 products by total sales", COLS, llm=llm, prompt_version="v2")
    v2 = get_prompts("v2")
    assert result.status is Status.OK and result.prompt_version == "v2"
    assert llm.calls[0][0] == v2.planner_system(COLS)
    assert llm.calls[1][0] == v2.coder_system(COLS)


def test_unknown_prompt_version_is_a_config_error_without_llm_calls(monkeypatch):
    monkeypatch.setenv("PROMPT_VERSION", "v99")
    llm = ScriptedLLM()
    result = run("anything", COLS, llm=llm)
    assert result.status is Status.CONFIG_ERROR
    assert "v99" in result.message and result.prompt_version is None
    assert llm.calls == []
