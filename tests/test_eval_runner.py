"""Phase 18: the eval runner, with a fake LLM only (conftest blocks the real client)."""

import json

from evals import run as runner

OUTLINE = (
    "1. Columns: product\n2. Filters: none\n3. Aggregations: SUM(amount) AS total_amount\n"
    "4. Grouping: product\n5. Ordering: total_amount DESC\n6. Limit: 3"
)
SQL = (
    "SELECT product, SUM(amount) AS total_amount FROM sales "
    "GROUP BY product ORDER BY total_amount DESC LIMIT 3"
)


class FakeLLM:
    """Planner: refuses requests about customers, otherwise one outline. Coder: one SQL."""

    def __init__(self):
        self.calls = 0

    def __call__(self, system, user):
        self.calls += 1
        if system.startswith("You plan"):
            return "CANNOT_ANSWER: no customer column" if "customer" in user else OUTLINE
        return SQL


def test_without_yes_nothing_is_called(capsys):
    llm = FakeLLM()
    code = runner.main(["--versions", "v1", "v2", "--cases", "top3_products_by_revenue"], llm=llm)
    assert code == 2 and llm.calls == 0
    out = capsys.readouterr().out
    assert "1 cases x 2 versions x 1 repeat(s) = up to 4 real LLM calls" in out
    assert "Nothing was called" in out


def test_planned_calls_upper_bound():
    assert runner.planned_calls([{}] * 18, ["v1", "v2", "v3"], 2) == 216


def test_full_run_with_fake_llm_writes_report_and_json(capsys, tmp_path, spans):
    llm = FakeLLM()
    argv = [
        "--versions", "v1", "v3",
        "--cases", "top3_products_by_revenue,refuse_customer",
        "--repeat", "2",
        "--yes",
    ]  # fmt: skip
    assert runner.main(argv, llm=llm, exporter=spans, results_dir=tmp_path) == 0
    # 2 versions x 2 repeats: the answer case costs 2 calls, the refusal 1
    assert llm.calls == 2 * 2 * (2 + 1)

    out = capsys.readouterr().out
    assert "| v1 | 4 | 100.0 | 100.0 | 100.0 |" in out
    assert "| top3_products_by_revenue | ✓ / ✓ | ✓ / ✓ |" in out
    assert "| refuse_customer | ✓ / ✓ | ✓ / ✓ |" in out

    (result_file,) = tmp_path.glob("*.json")
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert data["versions"] == ["v1", "v3"] and data["repeat"] == 2
    assert len(data["runs"]) == 8
    first = data["runs"][0]
    assert first["version"] == "v1" and first["sql"] == SQL and first["attempt"] == 1
    assert data["summary"]["v3"]["status_accuracy"] == 100.0


def test_failures_are_reported_per_case(capsys, tmp_path, spans):
    argv = ["--versions", "v2", "--cases", "units_per_product", "--yes"]
    assert runner.main(argv, llm=FakeLLM(), exporter=spans, results_dir=tmp_path) == 0
    out = capsys.readouterr().out
    # the fake SQL uses amount, which units_per_product doesn't select: the Reviewer rejects it
    assert "| units_per_product | ✗ review_failed (group_by:product, agg:SUM(quantity)) |" in out


def test_tokens_and_latency_come_from_spans(spans):
    class Usage(FakeLLM):
        def __call__(self, system, user):
            from opentelemetry import trace

            with trace.get_tracer("t").start_as_current_span("llm.complete") as s:
                s.set_attribute("llm.prompt_tokens", 100)
                s.set_attribute("llm.completion_tokens", 20)
                return super().__call__(system, user)

    case = runner.load_dataset()[1]  # top3_products_by_revenue
    scores, records = runner.run_eval([case], ["v1"], 1, spans, Usage())
    assert scores[0].prompt_tokens == 200 and scores[0].completion_tokens == 40
    assert scores[0].latency_ms >= 0 and records[0]["outline"] == OUTLINE


def test_unknown_version_or_case_is_rejected(capsys):
    import pytest

    with pytest.raises(SystemExit):
        runner.main(["--versions", "v9"], llm=FakeLLM())
    with pytest.raises(SystemExit):
        runner.main(["--cases", "nope"], llm=FakeLLM())


def test_rescore_applies_current_checks_without_llm_calls(capsys, tmp_path, spans):
    llm = FakeLLM()
    argv = ["--versions", "v1", "--cases", "top3_products_by_revenue,refuse_customer", "--yes"]
    runner.main(argv, llm=llm, exporter=spans, results_dir=tmp_path)
    (result_file,) = tmp_path.glob("*.json")
    calls_before = llm.calls
    capsys.readouterr()

    # Simulate an old file scored with a stricter rule: flip a stored check to False.
    data = json.loads(result_file.read_text(encoding="utf-8"))
    data["runs"][0]["checks"]["limit:3"] = False
    result_file.write_text(json.dumps(data), encoding="utf-8")

    assert runner.main(["--rescore", str(result_file)], llm=llm) == 0
    assert llm.calls == calls_before  # no LLM calls
    out = capsys.readouterr().out
    assert "Re-scored 2 stored runs" in out
    assert "| top3_products_by_revenue | ✓ |" in out  # judged again by the current checks
    assert "| refuse_customer | ✓ |" in out


def test_rescore_marks_checks_failed_for_non_ok_runs():
    case = {"id": "c", "expect": {"status": "ok", "checks": ["limit:3"]}}
    payload = {
        "runs": [
            {
                "case_id": "c", "version": "v1", "status": "cannot_answer", "review_passed": None,
                "sql": None, "prompt_tokens": 1, "completion_tokens": 1, "latency_ms": 1,
            }
        ]
    }  # fmt: skip
    (score,) = runner.rescore(payload, [case])
    assert not score.status_ok and score.checks == {"limit:3": False}
