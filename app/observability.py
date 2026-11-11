"""Logging and tracing (Phase 12).

- Logging: google-cloud-logging's StructuredLogHandler writes one JSON line per log record to
  stdout; Cloud Run turns it into a structured Cloud Logging entry (severity, fields) and links it
  to the active OpenTelemetry trace. It needs no credentials, so it works the same locally.
- Tracing: OpenTelemetry spans exported to Cloud Trace, sampling every run. Only switched on when
  running on Cloud Run (K_SERVICE is set) or with TRACING_ENABLED=true; otherwise OpenTelemetry's
  no-op tracer is used, so instrumented code runs unchanged locally and in tests.

Privacy: callers attach only sizes and metadata (lengths, column names, status, error *types*,
durations, token counts), never the API key, prompts, request text, outline or SQL.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from time import perf_counter
from typing import TextIO

from google.cloud.logging.handlers import StructuredLogHandler
from opentelemetry import trace
from opentelemetry.trace import Status as SpanStatus
from opentelemetry.trace import StatusCode

APP_LOGGER = "app"
DEFAULT_SERVICE_NAME = "sql-planner-pm"


def on_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


def service_name() -> str:
    """Trace service name: the Cloud Run service (K_SERVICE), else this app's default."""
    return os.environ.get("K_SERVICE") or DEFAULT_SERVICE_NAME


def tracing_enabled() -> bool:
    return on_cloud_run() or os.environ.get("TRACING_ENABLED", "").lower() == "true"


def project_id() -> str | None:
    """GCP project for log-trace links: env var, else the metadata server on Cloud Run."""
    pid = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if pid or not on_cloud_run():
        return pid
    import google.auth
    from google.auth.exceptions import GoogleAuthError

    try:
        return google.auth.default()[1]
    except (GoogleAuthError, OSError):  # no project: logs still work, just without trace links
        return None


# --- logging -----------------------------------------------------------------


def setup_logging(stream: TextIO | None = None) -> logging.Logger:
    """Attach the structured handler to the `app` logger once (safe on every Streamlit rerun)."""
    logger = logging.getLogger(APP_LOGGER)
    if not any(getattr(h, "_sql_planner", False) for h in logger.handlers):
        handler = StructuredLogHandler(stream=stream or sys.stdout, project_id=project_id())
        handler._sql_planner = True  # marker for idempotency
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    return logger


# --- tracing -----------------------------------------------------------------

_tracing_ready = False


def setup_tracing() -> bool:
    """Install the Cloud Trace exporter once, if enabled. Returns whether tracing is on."""
    global _tracing_ready
    if _tracing_ready or not tracing_enabled():
        return _tracing_ready

    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    from opentelemetry.resourcedetector.gcp_resource_detector import GoogleCloudResourceDetector
    from opentelemetry.sdk.resources import Resource, get_aggregated_resources
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    resource = get_aggregated_resources(
        [GoogleCloudResourceDetector(raise_on_error=False)],
        initial_resource=Resource.create({"service.name": service_name()}),
    )
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)  # every run is traced
    # Batch processor: spans are sent in the background, never slowing the UI.
    provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=project_id())))
    trace.set_tracer_provider(provider)
    _tracing_ready = True
    return True


@contextmanager
def span(tracer: trace.Tracer, name: str, durations: dict[str, int] | None = None, **attributes):
    """Start a span that records only the error TYPE on failure (never exception messages,
    which can contain user text), and optionally store its duration in `durations[name]`."""
    start = perf_counter()
    with tracer.start_as_current_span(
        name, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as current:
        try:
            yield current
        except BaseException as err:
            mark_error(current, type(err).__name__)
            raise
        finally:
            if durations is not None:
                durations[name] = round((perf_counter() - start) * 1000)


def mark_error(current: trace.Span, error_type: str) -> None:
    current.set_attribute("error.type", error_type)
    current.set_status(SpanStatus(StatusCode.ERROR, error_type))
