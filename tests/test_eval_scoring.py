"""Phase 18: the eval's scoring logic (pure functions) and dataset validity."""

import re

import pytest

from app.chain import ChainResult, Status
from app.reviewer import review_sql
from app.schema import COLUMN_NAMES, validate_selection
from evals.run import load_dataset
from evals.scoring import CHECK_KINDS, CaseScore, check, score_case, summarise

SQL = (
    "SELECT product, ROUND(SUM(amount), 2) AS total_amount FROM sales "
    "WHERE order_date >= DATE '2024-01-01' GROUP BY product ORDER BY total_amount DESC LIMIT 3"
)
MONTHLY = (
    "SELECT DATE_TRUNC(order_date, MONTH) AS m, COUNT(*) AS n FROM sales GROUP BY m ORDER BY m"
)
POSITIONAL = "SELECT EXTRACT(YEAR FROM order_date) AS y, MAX(amount) FROM sales GROUP BY 1"


@pytest.mark.parametrize(
    ("sql", "spec", "expected"),
    [
        (SQL, "group_by:product", True),
        (SQL, "group_by:amount", False),
        (MONTHLY, "group_by:order_date", True),  # through the alias m
        (POSITIONAL, "group_by:order_date", True),  # through GROUP BY 1
        (POSITIONAL, "group_by:amount", False),
        ("SELECT product FROM sales", "group_by:product", False),  # no GROUP BY
        (SQL, "agg:SUM(amount)", True),  # inside ROUND(...)
        (SQL, "agg:AVG(amount)", False),
        (SQL, "agg:SUM(quantity)", False),
        (MONTHLY, "agg:COUNT(*)", True),
        (SQL, "agg:COUNT(*)", False),
        (SQL, "func:ROUND", True),
        (MONTHLY, "func:DATE_TRUNC", True),
        (POSITIONAL, "func:extract", True),  # case-insensitive name
        (SQL, "func:DATE_TRUNC", False),
        (SQL, "filter:order_date", True),
        (SQL, "filter:product", False),
        (MONTHLY, "filter:order_date", False),  # no WHERE
        (SQL, "order:desc", True),
        (SQL, "order:asc", False),
        (MONTHLY, "order:asc", True),  # default direction
        (POSITIONAL, "order:asc", False),  # no ORDER BY
        (SQL, "limit:3", True),
        (SQL, "limit:5", False),
        (MONTHLY, "limit:3", False),
        ("SELEC nonsense FROM", "group_by:product", False),  # unparsable SQL fails every check
    ],
)
def test_check(sql, spec, expected):
    assert check(sql, spec) is expected


@pytest.mark.parametrize("spec", ["having:amount", "agg:SUM amount"])
def test_bad_check_specs_raise(spec):
    with pytest.raises(ValueError):
        check(SQL, spec)


CASE = {"id": "c1", "expect": {"status": "ok", "checks": ["group_by:product", "limit:5"]}}


def test_score_ok_run_evaluates_checks():
    result = ChainResult(
        Status.OK, None, ("product", "amount"), "o", SQL, review_sql(SQL, COLUMN_NAMES)
    )
    score = score_case(CASE, "v2", result, prompt_tokens=10, completion_tokens=5, latency_ms=7)
    assert score.status_ok and score.review_passed is True
    assert score.checks == {"group_by:product": True, "limit:5": False}
    assert score.checks_passed == 1 and score.prompt_tokens == 10


def test_score_wrong_status_fails_all_checks():
    result = ChainResult(Status.CANNOT_ANSWER, "no", ("product",))
    score = score_case(CASE, "v1", result)
    assert not score.status_ok and score.review_passed is None
    assert score.checks == {"group_by:product": False, "limit:5": False}


def test_score_accepts_any_of_several_expected_statuses():
    case = {"id": "inj", "expect": {"status": ["cannot_answer", "ok"]}}
    assert score_case(case, "v1", ChainResult(Status.CANNOT_ANSWER, "x")).status_ok
    bad = ChainResult(
        Status.REVIEW_FAILED,
        "x",
        (),
        "o",
        "DROP TABLE sales",
        review_sql("DROP TABLE sales", ["product"]),
    )
    score = score_case(case, "v1", bad)
    assert not score.status_ok and score.review_passed is False and score.checks == {}


def test_summarise_per_version():
    def s(version, status_ok, review, checks, tokens, latency, status="ok"):
        return CaseScore("c", version, status, status_ok, review, checks, tokens, 1, latency)

    scores = [
        s("v1", True, True, {"a": True, "b": False}, 100, 10),
        s("v1", False, None, {}, 50, 30, status="llm_error"),
        s("v2", True, True, {"a": True}, 200, 20),
    ]
    summary = summarise(scores)
    v1 = summary["v1"]
    assert v1["runs"] == 2 and v1["status_accuracy"] == 50.0
    assert v1["review_pass_rate"] == 100.0  # only runs that produced SQL count
    assert v1["check_pass_rate"] == 50.0
    assert v1["mean_prompt_tokens"] == 75 and v1["llm_errors"] == 1
    assert v1["p50_latency_ms"] == 20 and v1["p95_latency_ms"] == 29
    assert summary["v2"]["p50_latency_ms"] == 20 and summary["v2"]["check_pass_rate"] == 100.0


def test_summarise_handles_no_checks_and_no_sql():
    summary = summarise([CaseScore("c", "v1", "cannot_answer", True, None)])
    assert summary["v1"]["check_pass_rate"] is None and summary["v1"]["review_pass_rate"] is None


# --- dataset validity ------------------------------------------------------------------------

DATASET = load_dataset()


def test_dataset_ids_unique_and_size():
    ids = [c["id"] for c in DATASET]
    assert len(ids) == len(set(ids)) and 15 <= len(ids) <= 25


@pytest.mark.parametrize("case", DATASET, ids=lambda c: c["id"])
def test_dataset_case_is_valid(case):
    validate_selection(case["columns"])
    statuses = case["expect"]["status"]
    statuses = [statuses] if isinstance(statuses, str) else statuses
    assert set(statuses) <= {s.value for s in Status}
    for spec in case["expect"].get("checks", []):
        kind, _, arg = spec.partition(":")
        assert kind in CHECK_KINDS
        named = re.findall(r"[a-z_]+", arg) if kind in ("group_by", "agg", "filter") else []
        for column in named:  # checks only name columns the case selects
            assert column in case["columns"], (spec, column)


def test_dataset_covers_refusals_and_injections():
    assert sum(c["expect"]["status"] == "cannot_answer" for c in DATASET) >= 3
    assert sum(c["id"].startswith("injection_") for c in DATASET) >= 2
