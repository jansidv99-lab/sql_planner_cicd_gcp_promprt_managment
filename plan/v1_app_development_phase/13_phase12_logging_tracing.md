# Phase 12 — Logging and Tracing (detailed plan)

**Status:** DONE. Decisions: google-cloud-logging (`StructuredLogHandler`), OpenTelemetry → Cloud Trace, every run sampled, privacy rule, runtime SA granted `cloudtrace.agent`. PR #4 deployed; production check found log entries had no trace link (log written after the span closed): fixed in PR #5 with a regression test. Verified on revision `sql-planner-00005`: log entry (status ok, durations, no request text) links to trace `718b7d87…` with spans chain.run → planner/coder (→ llm.complete with token counts) / reviewer. Note: the Cloud Trace v1 *list* endpoint returned no traces; fetching a trace **by ID** works.
**Part of:** [00_master_plan.md](00_master_plan.md) (split out of Phase 6 at your request)
**Depends on:** Phase 6 (chain to instrument) ✅, Phase 2 (LLM client) ✅, Phase 7 (UI entry point) ✅, Phase 11 (Cloud Run + CD, to verify in production) ✅.
**Unblocks:** nothing; this is the last planned phase.

## Goal

See what the app does in production without reading code:
- **Logs (Cloud Logging):** one structured line per chain run: status, columns, step durations, token usage. Searchable and filterable by status.
- **Traces (Cloud Trace):** one trace per "Generate SQL" click, with a span per step (Planner, Coder, Reviewer) and per LLM call, so you can see **which step was slow or failed**.
- Log lines **link to their trace** in the Cloud console.

```
Trace: chain.run  (status=ok, 1.9 s)
 ├─ planner        0.9 s
 │   └─ llm.complete   0.9 s  (model=gpt-4o-mini, tokens in/out)
 ├─ coder          0.9 s
 │   └─ llm.complete   0.8 s
 └─ reviewer       0.004 s (passed=true, errors=0)

Log: {"severity":"INFO","message":"chain finished","status":"ok","columns":["product","amount"],
      "request_chars":29,"duration_ms":{"planner":912,"coder":861,"reviewer":4},
      "logging.googleapis.com/trace":"projects/<project>/traces/<trace-id>"}
```

## Privacy rule (applies to every step)

**Never logged or attached to spans:** the API key, the prompts, the user's request text, the outline, the SQL. Only **sizes and metadata** (lengths, column names, status, error *types*, durations, token counts). The request and SQL can contain user-typed values (e.g. product names), so they stay out of telemetry.

## Current state (checked 2026-11-10)

| Item | State |
|---|---|
| Cloud Trace, Cloud Logging APIs | already enabled |
| Logs | Cloud Run already captures stdout/stderr, but the app writes nothing structured; Streamlit's own logs only |
| Runtime SA `sql-planner-runtime` | can read its secret only; **can't write traces yet** (needs `roles/cloudtrace.agent`) |
| Packages (latest) | `opentelemetry-sdk` 1.45.2, `opentelemetry-exporter-gcp-trace` 1.15.0, `opentelemetry-resourcedetector-gcp` 1.14.0 |

---

## Steps

### Step 12.1 — `app/observability.py`: structured JSON logging
- **Why:** Cloud Run turns each JSON line on stdout into a structured log entry; `severity` sets the level and `logging.googleapis.com/trace` links the entry to its trace. No extra library needed.
- **What:**
  - `JsonFormatter` (stdlib `logging.Formatter`): one JSON object per line with `severity`, `message`, `logger`, `time`, any `extra` fields, and the current trace ID (when tracing is on) as `logging.googleapis.com/trace` + `logging.googleapis.com/spanId`.
  - `setup_logging()`: attaches the formatter to the `app` logger on stdout, level `INFO` (override with env var `LOG_LEVEL`). Idempotent (safe to call on every Streamlit rerun).
  - Locally the same JSON goes to the terminal (one consistent format everywhere).
- **Depends on:** nothing.

### Step 12.2 — `app/observability.py`: tracing setup
- **Why:** OpenTelemetry is the standard tracing API; the GCP exporter sends spans to Cloud Trace.
- **What:**
  - `setup_tracing()`: creates a `TracerProvider` with the GCP resource detector (service name, revision, region) and a `BatchSpanProcessor` + `CloudTraceSpanExporter` (sends in the background; never slows the UI).
  - **On only on Cloud Run** (detected by the `K_SERVICE` env var Cloud Run sets) or when `TRACING_ENABLED=true`; **off locally and in tests** by default (no GCP credentials needed).
  - When off, OpenTelemetry's no-op tracer is used, so the instrumented code runs unchanged and costs nothing.
  - `get_tracer()` helper; idempotent setup.
- **New dependencies** (pinned in `requirements.txt`): `opentelemetry-sdk`, `opentelemetry-exporter-gcp-trace`, `opentelemetry-resourcedetector-gcp`. Image grows by roughly 30–50 MB.
- **Depends on:** nothing.

### Step 12.3 — Instrument the chain (`app/chain.py`)
- **Why:** this is where one user action becomes one trace and one log line.
- **What:**
  - Root span `chain.run` with attributes `status`, `columns`, `request_chars`.
  - Child spans `planner`, `coder`, `reviewer`; the reviewer span adds `review.passed` and `review.error_count`.
  - Expected failures (status ≠ ok) set the span status to error with the **error type** (e.g. `CannotAnswerError`), not the message text.
  - One log line `chain finished` at the end of every run (INFO for ok/cannot_answer/input_error, WARNING for review_failed, ERROR for coder/llm errors) with status, columns, request length and per-step `duration_ms`.
  - `ChainResult` and the chain's behaviour **don't change**; all existing chain tests must pass unmodified.
- **Depends on:** 12.1, 12.2.

### Step 12.4 — Instrument the LLM call (`app/llm.py`)
- **Why:** LLM calls are the slow and costly part; their timing and token usage are the most useful production numbers.
- **What:** span `llm.complete` with `llm.model`, `llm.prompt_tokens`, `llm.completion_tokens` (from the API response's usage data, when present), and the error type on failure. No prompt or reply text.
- **Depends on:** 12.2.

### Step 12.5 — Initialise once in the UI (`streamlit_app.py`)
- **Why:** Streamlit reruns the script on every interaction; setup must happen once per process.
- **What:** call `setup_logging()` and `setup_tracing()` inside a `@st.cache_resource` function at the top of the script.
- **Depends on:** 12.1, 12.2.

### Step 12.6 — Tests (`tests/test_observability.py` + additions)
- **Why:** prove the telemetry is correct **and** private, with no GCP access.
- **How:** OpenTelemetry's `InMemorySpanExporter` for spans; `capsys`/`caplog` for log lines.
- **Cases:**
  1. JSON formatter: valid JSON, `severity` mapped from the level, extra fields included, trace fields added when a span is active;
  2. setup functions are idempotent; tracing is off without `K_SERVICE`/`TRACING_ENABLED`;
  3. an `ok` chain run produces spans `chain.run` → `planner`, `coder`, `reviewer` (+ `llm.complete` when the real `complete` path is used with a fake client), with the expected attributes;
  4. a failing run (e.g. `cannot_answer`, `llm_error`) marks the span as error with the error type;
  5. one `chain finished` log line per run with status, columns and durations;
  6. **privacy:** a distinctive request string, outline and SQL never appear in any log line or span attribute;
  7. token counts recorded on the LLM span when the response includes usage.
- Coverage of `app/` stays ≥ 90 %.
- **Depends on:** 12.3, 12.4, 12.5.

### Step 12.7 — Permission for the runtime SA to write traces
- **Why:** without it, the exporter is refused and traces silently don't appear.
- **What:** add to `scripts/gcp_setup.sh` (re-runnable): grant `roles/cloudtrace.agent` to `sql-planner-runtime` (project level; this role can only **write** trace data). I re-run the script with your approval; existing resources are skipped.
- **Depends on:** nothing (can be done before the deploy).

### Step 12.8 — Branch, PR, deploy
- Branch `phase-12-observability` → PR → CI green → **you approve** → merge → CI → CD deploys automatically (no `cd.yml` change needed: Cloud Run sets `K_SERVICE`, which switches tracing on).
- **Depends on:** 12.6, 12.7.

### Step 12.9 — Verify in production
1. **You** generate one query on the live URL (2 real API calls).
2. I read the log entry back: `gcloud logging read 'resource.labels.service_name="sql-planner" AND jsonPayload.message="chain finished"' --limit 1`; check fields, check that no request text is present.
3. I fetch the linked trace from the Cloud Trace API and check it has the `chain.run` → `planner`/`coder`/`reviewer` → `llm.complete` spans; I give you the console link to see it.
4. Update `CLAUDE.md` (observability notes) and plan statuses in a small follow-up PR.
- **Depends on:** 12.8.

---

## Step dependencies

```
12.1 logging ─┐
12.2 tracing ─┼─► 12.3 chain ─┐
              ├─► 12.4 llm ───┼─► 12.6 tests ─┐
              └─► 12.5 UI ────┘               ├─► 12.8 PR/merge/deploy ─► 12.9 verify
12.7 IAM role (your approval) ────────────────┘
```

## Files changed in this phase

- `app/observability.py` (new)
- `app/chain.py`, `app/llm.py` (instrumentation only; behaviour unchanged)
- `streamlit_app.py` (one-time setup call)
- `requirements.txt` (+3 OpenTelemetry packages)
- `scripts/gcp_setup.sh` (+ `cloudtrace.agent` role)
- `tests/test_observability.py` (new), small additions to existing tests
- `CLAUDE.md`

## Cost notes

Cloud Logging and Cloud Trace have free monthly allowances (Logging: 50 GiB ingestion; Trace: first 2.5 million spans). At about 5 spans and 1 log line per click, this app stays far inside them.

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Logging: stdlib JSON formatter (no new dependency) instead of the `google-cloud-logging` library? | **Stdlib JSON**: Cloud Run parses JSON stdout natively; fewer dependencies |
| 2 | Tracing: OpenTelemetry + Cloud Trace exporter (+3 packages, ~30–50 MB image growth)? | **Yes** |
| 3 | Trace sampling: record **every** run? | **Yes, 100 %**: traffic is low and well within the free tier; can be lowered with one setting |
| 4 | Privacy: telemetry holds only sizes/metadata, never request text, outline or SQL? | **Yes** |
| 5 | I re-run `scripts/gcp_setup.sh` to grant the runtime SA `roles/cloudtrace.agent`? | **Yes** |
