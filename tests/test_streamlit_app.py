from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.chain import ChainResult, Status
from app.config import ConfigError, Settings
from app.reviewer import ReviewResult

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
COLS = ("product", "amount")
OUTLINE = "1. Columns: product\n3. Aggregations: SUM(amount) AS total_sales"
SQL = "SELECT product, SUM(amount) AS total_sales FROM sales GROUP BY product"
FORMATTED = "SELECT\n  product,\n  SUM(amount) AS total_sales\nFROM sales\nGROUP BY\n  product"


@pytest.fixture
def fake_settings(monkeypatch):
    monkeypatch.setattr(
        "app.config.load_settings",
        lambda env=None: Settings("sk-test", "https://example.test/v1", "test-model"),
    )


@pytest.fixture
def fake_chain(monkeypatch, fake_settings):
    """Replace chain.run; set `.result` to choose what it returns, `.calls` records calls."""

    class Fake:
        def __init__(self):
            self.result = None
            self.calls = []

        def __call__(self, request, columns):
            self.calls.append((request, list(columns)))
            return self.result

    fake = Fake()
    monkeypatch.setattr("app.chain.run", fake)
    return fake


def _submit(result, fake, columns=("product", "amount"), request="top products"):
    fake.result = result
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.multiselect[0].set_value(list(columns))
    at.text_area[0].input(request)
    at.button[0].click().run()
    assert not at.exception
    return at


def test_page_loads_with_schema_and_four_column_options(fake_settings):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value == "SQL Script Generator"
    assert len(at.table) == 1
    assert list(at.multiselect[0].options) == ["order_date", "quantity", "product", "amount"]
    assert at.multiselect[0].value == []  # none selected by default


def test_missing_config_shows_error_and_hides_form(monkeypatch):
    def raise_config(env=None):
        raise ConfigError("Missing required settings: OPENAI_API_KEY")

    monkeypatch.setattr("app.config.load_settings", raise_config)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert "OPENAI_API_KEY" in at.error[0].value
    assert len(at.multiselect) == 0 and len(at.button) == 0


def test_ok_result(fake_chain):
    review = ReviewResult(True, (), FORMATTED)
    at = _submit(ChainResult(Status.OK, None, COLS, OUTLINE, SQL, review), fake_chain)
    assert "passed all checks" in at.success[0].value
    assert at.text[0].value == OUTLINE
    assert at.code[0].value == FORMATTED
    assert len(at.get("download_button")) == 1


def test_review_failed_lists_errors_without_download(fake_chain):
    review = ReviewResult(False, ("Column not selected: quantity", "Unknown table: x"), FORMATTED)
    result = ChainResult(Status.REVIEW_FAILED, "failed", COLS, OUTLINE, SQL, review)
    at = _submit(result, fake_chain)
    assert "failed the review checks" in at.error[0].value
    assert at.code[0].value == FORMATTED
    review_md = " ".join(m.value for m in at.markdown)
    assert "Column not selected: quantity" in review_md and "Unknown table: x" in review_md
    assert len(at.get("download_button")) == 0


def test_cannot_answer_shows_reason_and_no_sql(fake_chain):
    result = ChainResult(Status.CANNOT_ANSWER, "Can't answer: no date column", COLS)
    at = _submit(result, fake_chain)
    assert "no date column" in at.warning[0].value
    assert "selecting more columns" in at.warning[0].value
    assert len(at.code) == 0


def test_llm_error_shows_message(fake_chain):
    at = _submit(ChainResult(Status.LLM_ERROR, "LLM request timed out", COLS), fake_chain)
    assert "LLM request timed out" in at.error[0].value


def test_submit_passes_inputs_to_chain_once(fake_chain):
    _submit(
        ChainResult(Status.INPUT_ERROR, "Enter a request"),
        fake_chain,
        columns=("amount", "order_date"),
        request="monthly totals",
    )
    assert len(fake_chain.calls) == 1
    request, columns = fake_chain.calls[0]
    assert request == "monthly totals"
    assert sorted(columns) == ["amount", "order_date"]


# --- Phase 16: prompt version caption ------------------------------------------------------


def test_prompt_version_caption_shown(fake_chain):
    review = ReviewResult(True, [], FORMATTED)
    result = ChainResult(Status.OK, None, COLS, OUTLINE, SQL, review, prompt_version="v2")
    at = _submit(result, fake_chain)
    assert any("Prompt version: v2" in c.value for c in at.caption)


def test_config_error_shown(fake_chain):
    at = _submit(ChainResult(Status.CONFIG_ERROR, "Unknown prompt version 'v9'"), fake_chain)
    assert "Unknown prompt version" in at.error[0].value
    assert not any("Prompt version:" in c.value for c in at.caption)
