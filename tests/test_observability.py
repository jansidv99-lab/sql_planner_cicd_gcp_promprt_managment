import io
import json
import logging
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from app import observability
from app.chain import Status, run
from app.config import Settings
from app.llm import LLMError, complete

# Distinctive user text: must never appear in logs or span attributes.
SECRET_REQUEST = "zebra-unicorn-request-7731"
OUTLINE = "1. Columns: product\n3. Aggregations: SUM(amount) AS total_xylophone"
SQL = "SELECT product, SUM(amount) AS total_xylophone FROM sales GROUP BY product"
COLS = ["product", "amount"]
SETTINGS = Settings("sk-secret-value", "https://example.test/v1", "test-model")


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def __call__(self, system, user):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _by_name(spans):
    return {s.name: s for s in spans.get_finished_spans()}


def _chain_log(caplog):
    (record,) = [r for r in caplog.records if r.getMessage() == "chain finished"]
    return record


# --- logging setup ------------------------------------------------------------


def test_setup_logging_writes_structured_json_with_trace_link(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    stream = io.StringIO()
    logger = observability.setup_logging(stream=stream)
    with trace.get_tracer("t").start_as_current_span("s"):
        logging.getLogger("app.chain").warning("hello", extra={"json_fields": {"status": "ok"}})

    entry = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert entry["message"] == "hello"
    assert entry["severity"] == "WARNING"
    assert entry["status"] == "ok"
    assert entry["logging.googleapis.com/trace"].startswith("projects/demo-project/traces/")
    assert logger.propagate is False


def test_setup_logging_is_idempotent():
    observability.setup_logging(stream=io.StringIO())
    observability.setup_logging(stream=io.StringIO())
    marked = [h for h in logging.getLogger("app").handlers if getattr(h, "_sql_planner", False)]
    assert len(marked) == 1


def test_log_level_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")
    assert observability.setup_logging(stream=io.StringIO()).level == logging.WARNING


# --- tracing setup ------------------------------------------------------------


def test_tracing_off_by_default():
    assert observability.tracing_enabled() is False
    assert observability.setup_tracing() is False


@pytest.mark.parametrize(
    ("name", "value"), [("K_SERVICE", "sql-planner-pm"), ("TRACING_ENABLED", "true")]
)
def test_tracing_enabled_on_cloud_run_or_flag(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert observability.tracing_enabled() is True


def test_service_name_defaults_to_this_app(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert observability.service_name() == "sql-planner-pm"


def test_service_name_follows_cloud_run_service(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "some-other-service")
    assert observability.service_name() == "some-other-service"


def test_setup_tracing_installs_cloud_trace_exporter_sampling_every_run(monkeypatch):
    from opentelemetry.exporter import cloud_trace

    installed = {}
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setattr(observability, "_tracing_ready", False)
    monkeypatch.setattr(
        cloud_trace, "CloudTraceSpanExporter", lambda project_id: ("exporter", project_id)
    )
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.export.BatchSpanProcessor", lambda exp: SimpleNamespace(exp=exp)
    )
    monkeypatch.setattr(
        observability.trace, "set_tracer_provider", lambda p: installed.setdefault("p", p)
    )
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.TracerProvider.add_span_processor",
        lambda self, proc: installed.setdefault("proc", proc),
    )

    assert observability.setup_tracing() is True
    assert observability.setup_tracing() is True  # idempotent: second call doesn't reinstall
    provider = installed["p"]
    assert provider.sampler.get_description() == "AlwaysOnSampler"
    assert provider.resource.attributes["service.name"] == "sql-planner-pm"
    assert installed["proc"].exp == ("exporter", "demo-project")


def test_project_id_from_metadata_on_cloud_run(monkeypatch):
    import google.auth

    monkeypatch.setenv("K_SERVICE", "sql-planner-pm")
    monkeypatch.setattr(google.auth, "default", lambda: (None, "meta-project"))
    assert observability.project_id() == "meta-project"


def test_project_id_none_when_unavailable(monkeypatch):
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    def fail():
        raise DefaultCredentialsError("no creds")

    monkeypatch.setenv("K_SERVICE", "sql-planner-pm")
    monkeypatch.setattr(google.auth, "default", fail)
    assert observability.project_id() is None  # logs still work, without trace links


# --- chain instrumentation ------------------------------------------------------


def test_ok_run_produces_spans_and_one_log_line(spans, caplog):
    caplog.set_level(logging.INFO, logger="app")
    result = run(SECRET_REQUEST, COLS, llm=ScriptedLLM(OUTLINE, SQL))
    assert result.status is Status.OK

    named = _by_name(spans)
    root = named["chain.run"]
    assert {"planner", "coder", "reviewer"} <= named.keys()
    for child in ("planner", "coder", "reviewer"):
        assert named[child].parent.span_id == root.context.span_id
    assert root.attributes["status"] == "ok"
    assert list(root.attributes["columns"]) == COLS
    assert root.attributes["request_chars"] == len(SECRET_REQUEST)
    assert root.status.status_code is not StatusCode.ERROR
    assert named["reviewer"].attributes["review.passed"] is True

    record = _chain_log(caplog)
    fields = record.json_fields
    assert record.levelno == logging.INFO
    assert fields["status"] == "ok" and fields["error_type"] is None
    assert set(fields["duration_ms"]) == {"planner", "coder", "reviewer"}


@pytest.mark.parametrize(
    ("replies", "status", "error_type", "level"),
    [
        (
            ("CANNOT_ANSWER: no date column",),
            Status.CANNOT_ANSWER,
            "CannotAnswerError",
            logging.INFO,
        ),
        ((LLMError("LLM request timed out"),), Status.LLM_ERROR, "LLMError", logging.ERROR),
        (
            (OUTLINE, "SELECT quantity FROM sales"),
            Status.REVIEW_FAILED,
            "ReviewFailed",
            logging.WARNING,
        ),
    ],
)
def test_failed_runs_mark_error_type(spans, caplog, replies, status, error_type, level):
    caplog.set_level(logging.INFO, logger="app")
    result = run(SECRET_REQUEST, COLS, llm=ScriptedLLM(*replies))
    assert result.status is status

    root = _by_name(spans)["chain.run"]
    assert root.status.status_code is StatusCode.ERROR
    assert root.attributes["error.type"] == error_type
    record = _chain_log(caplog)
    assert record.levelno == level
    assert record.json_fields["error_type"] == error_type


def test_unexpected_exception_logged_and_propagates(spans, caplog):
    caplog.set_level(logging.INFO, logger="app")
    with pytest.raises(ZeroDivisionError):
        run(SECRET_REQUEST, COLS, llm=ScriptedLLM(ZeroDivisionError()))
    (record,) = [r for r in caplog.records if r.getMessage() == "chain crashed"]
    assert record.json_fields["error_type"] == "ZeroDivisionError"
    assert _by_name(spans)["chain.run"].attributes["error.type"] == "ZeroDivisionError"


@pytest.mark.parametrize(
    "replies",
    [
        (OUTLINE, SQL),
        ("CANNOT_ANSWER: zebra-unicorn-request-7731 needs a region",),
        (OUTLINE, "SELECT total_xylophone FROM zebra_unicorn_request_7731"),
    ],
)
def test_privacy_no_user_text_in_logs_or_spans(spans, caplog, replies):
    caplog.set_level(logging.DEBUG, logger="app")
    run(SECRET_REQUEST, COLS, llm=ScriptedLLM(*replies))

    telemetry = [
        json.dumps(getattr(r, "json_fields", {}), default=str) + r.getMessage()
        for r in caplog.records
    ]
    for s in spans.get_finished_spans():
        telemetry.append(json.dumps(dict(s.attributes), default=str))
        telemetry.append(s.status.description or "")
    blob = " ".join(telemetry).lower()
    for forbidden in ("zebra", "unicorn", "xylophone", "select ", "aggregations"):
        assert forbidden not in blob, forbidden


# --- LLM instrumentation --------------------------------------------------------


class FakeClient:
    def __init__(self, content="SELECT 1", usage=None, error=None):
        self._content, self._usage, self._error = content, usage, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        if self._error:
            raise self._error
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=self._usage)


def test_llm_span_records_model_and_tokens(spans):
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30)
    complete("s", "u", settings=SETTINGS, client=FakeClient(usage=usage))
    llm_span = _by_name(spans)["llm.complete"]
    assert llm_span.attributes["llm.model"] == "test-model"
    assert llm_span.attributes["llm.prompt_tokens"] == 120
    assert llm_span.attributes["llm.completion_tokens"] == 30


def test_llm_span_records_sdk_error_type(spans):
    import httpx2
    import openai

    request = httpx2.Request("POST", "https://example.test/v1/chat/completions")
    with pytest.raises(LLMError):
        complete(
            "s",
            "u",
            settings=SETTINGS,
            client=FakeClient(error=openai.APITimeoutError(request=request)),
        )
    llm_span = _by_name(spans)["llm.complete"]
    assert llm_span.attributes["llm.sdk_error_type"] == "APITimeoutError"
    assert llm_span.attributes["error.type"] == "LLMError"
    assert "sk-secret-value" not in json.dumps(dict(llm_span.attributes))


def test_chain_log_entry_links_to_its_own_trace(monkeypatch, spans):
    """Regression: the `chain finished` line must be written inside the `chain.run` span,
    otherwise Cloud Logging can't link it to the trace (seen in production, Phase 12)."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    stream = io.StringIO()
    observability.setup_logging(stream=stream)

    run(SECRET_REQUEST, COLS, llm=ScriptedLLM("CANNOT_ANSWER: no date column"))

    entries = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    (entry,) = [e for e in entries if e.get("message") == "chain finished"]
    root = _by_name(spans)["chain.run"]
    trace_id = format(root.context.trace_id, "032x")
    assert entry["logging.googleapis.com/trace"] == f"projects/demo-project/traces/{trace_id}"
