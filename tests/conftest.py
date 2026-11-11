"""Shared test setup: no test may ever reach the real LLM or GCP; spans go to memory."""

import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# One in-memory tracer provider for the whole test session (OpenTelemetry allows setting the
# global provider only once). Tests read finished spans through the `spans` fixture.
_SPAN_EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))
trace.set_tracer_provider(_provider)


@pytest.fixture(autouse=True)
def _block_real_llm(monkeypatch):
    """Fail any test that tries to build the real OpenAI client.

    Tests that need an LLM pass a fake (`llm=` or `client=`), so this never triggers for them.
    """

    def _blocked(*_args, **_kwargs):
        pytest.fail("Tests must not call the real LLM; pass a fake llm/client instead")

    monkeypatch.setattr("app.llm.get_client", _blocked)


@pytest.fixture(autouse=True)
def _isolate_observability(monkeypatch):
    """Keep tracing off (no Cloud Run env) and undo any logging setup after each test."""
    for name in ("K_SERVICE", "TRACING_ENABLED", "GOOGLE_CLOUD_PROJECT", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    # Phase 16: a PROMPT_VERSION in a local .env must not change test results.
    monkeypatch.setenv("PROMPT_VERSION", "v1")
    yield
    logger = logging.getLogger("app")
    for handler in [h for h in logger.handlers if getattr(h, "_sql_planner", False)]:
        logger.removeHandler(handler)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


@pytest.fixture
def spans():
    """Finished spans recorded during the test."""
    _SPAN_EXPORTER.clear()
    return _SPAN_EXPORTER
