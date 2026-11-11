import pytest

from app.coder import CoderError, extract_sql, write_sql
from app.llm import LLMError
from app.prompts import get_prompts
from app.schema import SchemaError

OUTLINE = "1. Columns: product, amount\n3. Aggregations: SUM(amount) AS total_sales"
SQL = "SELECT product, SUM(amount) AS total_sales\nFROM sales\nGROUP BY product"


class FakeLLM:
    def __init__(self, reply=SQL, error=None):
        self.calls = []
        self.reply = reply
        self.error = error

    def __call__(self, system, user):
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.mark.parametrize(
    "reply",
    [
        SQL,
        f"  {SQL}\n\n",
        f"```sql\n{SQL}\n```",
        f"```SQL\n{SQL}\n```",
        f"```\n{SQL}\n```",
        f"Here is the query:\n```sql\n{SQL}\n```\nIt groups by product.",
        f"```sql\n{SQL}",  # unclosed fence
    ],
)
def test_extract_sql_variants(reply):
    assert extract_sql(reply) == SQL


@pytest.mark.parametrize("reply", ["", "   ", "```sql\n```", "``````", "```sql"])
def test_extract_sql_empty_raises(reply):
    with pytest.raises(CoderError, match="no SQL"):
        extract_sql(reply)


def test_write_sql_returns_clean_sql():
    llm = FakeLLM(reply=f"```sql\n{SQL}\n```")
    assert write_sql(OUTLINE, ["product", "amount"], llm=llm) == SQL


def test_prompt_has_only_selected_columns():
    llm = FakeLLM()
    write_sql(OUTLINE, ["product", "amount"], llm=llm)
    ((_, user),) = llm.calls
    assert "- product" in user and "- amount" in user
    assert "order_date" not in user and "quantity" not in user


def test_outline_in_tags_and_coder_system_prompt():
    llm = FakeLLM()
    write_sql(OUTLINE, ["product", "amount"], llm=llm)
    ((system, user),) = llm.calls
    assert system == get_prompts("v1").coder_system(["product", "amount"])
    assert f"<outline>\n{OUTLINE}\n</outline>" in user


def test_system_prompt_rules():
    system = get_prompts("v1").coder_system(["product", "amount"])
    assert "BigQuery" in system
    assert "SELECT *" in system and "Never" in system


@pytest.mark.parametrize("outline", ["", "   ", None])
def test_empty_outline_rejected_without_llm_call(outline):
    llm = FakeLLM()
    with pytest.raises(CoderError):
        write_sql(outline, ["amount"], llm=llm)
    assert llm.calls == []


def test_invalid_columns_rejected_without_llm_call():
    llm = FakeLLM()
    with pytest.raises(SchemaError):
        write_sql(OUTLINE, ["region"], llm=llm)
    assert llm.calls == []


def test_llm_error_passes_through():
    err = LLMError("LLM request timed out")
    with pytest.raises(LLMError) as exc:
        write_sql(OUTLINE, ["amount"], llm=FakeLLM(error=err))
    assert exc.value is err


def test_llm_called_exactly_once():
    llm = FakeLLM()
    write_sql(OUTLINE, ["product", "amount"], llm=llm)
    assert len(llm.calls) == 1
