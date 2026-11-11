"""Prompt eval runner (Phase 18). LOCAL ONLY: it calls the real LLM and costs requests.

    python -m evals.run --versions v1 v2 v3 [--cases id1,id2] [--repeat N] --yes
    python -m evals.run --rescore evals/results/<file>.json     (no LLM calls)

Runs every dataset case through the real chain (`app.chain.run(..., prompt_version=v)`) for each
version, scores the results deterministically (evals/scoring.py), prints a markdown report and
writes full JSON to evals/results/ (gitignored). Without --yes it only prints the planned number
of LLM calls and exits.

Token counts and latency come from the app's own OpenTelemetry spans (`llm.complete`,
`chain.run`), read from an in-memory exporter: the app code needs no eval-specific changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import chain
from app.llm import LLMFn, complete
from app.prompts import available_versions
from evals.scoring import CaseScore, check, expected_statuses, score_case, summarise

DATASET = Path(__file__).parent / "dataset.json"
RESULTS_DIR = Path(__file__).parent / "results"


def load_dataset(path: Path = DATASET) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def planned_calls(cases: Sequence[dict], versions: Sequence[str], repeat: int) -> int:
    """Upper bound: 2 calls per run (Planner + Coder); refusals stop after 1."""
    return len(cases) * len(versions) * repeat * 2


def install_exporter() -> InMemorySpanExporter:
    """Route the app's spans to memory so tokens and latency can be read per run."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


def _usage(exporter: InMemorySpanExporter) -> tuple[int, int, int]:
    spans = exporter.get_finished_spans()
    prompt = sum(s.attributes.get("llm.prompt_tokens", 0) for s in spans)
    completion = sum(s.attributes.get("llm.completion_tokens", 0) for s in spans)
    roots = [s for s in spans if s.name == "chain.run"]
    latency = round((roots[-1].end_time - roots[-1].start_time) / 1e6) if roots else 0
    return prompt, completion, latency


def run_eval(
    cases: Sequence[dict],
    versions: Sequence[str],
    repeat: int,
    exporter: InMemorySpanExporter,
    llm: LLMFn = complete,
    progress: Callable[[str], None] = lambda _msg: None,
) -> tuple[list[CaseScore], list[dict]]:
    """Run sequentially (stays inside rate limits). Returns scores and full run records."""
    scores, records = [], []
    for case in cases:
        for version in versions:
            for attempt in range(1, repeat + 1):
                exporter.clear()
                result = chain.run(case["request"], case["columns"], llm, prompt_version=version)
                prompt, completion, latency = _usage(exporter)
                score = score_case(
                    case,
                    version,
                    result,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    latency_ms=latency,
                )
                scores.append(score)
                records.append(
                    {
                        **asdict(score),
                        "attempt": attempt,
                        "outline": result.outline,
                        "sql": result.sql,
                        "review_errors": list(result.review.errors) if result.review else [],
                        "message": result.message,
                    }
                )
                mark = "✓" if score.status_ok and all(score.checks.values()) else "✗"
                progress(f"{mark} {case['id']} [{version} #{attempt}] {score.status}")
    return scores, records


def rescore(payload: dict, cases: Sequence[dict]) -> list[CaseScore]:
    """Re-score stored runs with the CURRENT dataset checks: no LLM calls.

    Used after fixing a check, so every version is judged by the same corrected rule.
    """
    by_id = {c["id"]: c for c in cases}
    scores = []
    for run in payload["runs"]:
        case = by_id[run["case_id"]]
        specs = case["expect"].get("checks", [])
        accepted = run["status"] == chain.Status.OK.value
        scores.append(
            CaseScore(
                case_id=run["case_id"],
                version=run["version"],
                status=run["status"],
                status_ok=run["status"] in expected_statuses(case),
                review_passed=run["review_passed"],
                checks={spec: accepted and check(run["sql"], spec) for spec in specs},
                prompt_tokens=run["prompt_tokens"],
                completion_tokens=run["completion_tokens"],
                latency_ms=run["latency_ms"],
            )
        )
    return scores


def render_report(scores: Sequence[CaseScore], versions: Sequence[str]) -> str:
    summary = summarise(scores)
    lines = [
        (
            "| version | runs | status acc. % | review pass % | checks pass % "
            "| prompt tok | completion tok | p50 ms | p95 ms | LLM errors |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in versions:
        m = summary[v]
        lines.append(
            f"| {v} | {m['runs']} | {m['status_accuracy']} | {m['review_pass_rate']} "
            f"| {m['check_pass_rate']} | {m['mean_prompt_tokens']} | {m['mean_completion_tokens']} "
            f"| {m['p50_latency_ms']} | {m['p95_latency_ms']} | {m['llm_errors']} |"
        )

    lines += ["", "| case | " + " | ".join(versions) + " |", "|---|" + "---|" * len(versions)]
    cells: dict[tuple[str, str], list[str]] = {}
    for s in scores:
        ok = s.status_ok and all(s.checks.values())
        failed = [c for c, passed in s.checks.items() if not passed]
        detail = "✓" if ok else f"✗ {s.status}" + (f" ({', '.join(failed)})" if failed else "")
        cells.setdefault((s.case_id, s.version), []).append(detail)
    for case_id in dict.fromkeys(s.case_id for s in scores):
        row = [" / ".join(cells.get((case_id, v), ["-"])) for v in versions]
        lines.append(f"| {case_id} | " + " | ".join(row) + " |")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    llm: LLMFn = complete,
    exporter: InMemorySpanExporter | None = None,
    results_dir: Path = RESULTS_DIR,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument("--versions", nargs="+", default=list(available_versions()))
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--yes", action="store_true", help="really call the LLM")
    parser.add_argument("--rescore", type=Path, help="re-score a results file (no LLM calls)")
    args = parser.parse_args(argv)

    if args.rescore:
        payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        scores = rescore(payload, load_dataset())
        print(f"Re-scored {len(scores)} stored runs with the current checks (no LLM calls).\n")
        print(render_report(scores, payload["versions"]))
        return 0

    unknown = [v for v in args.versions if v not in available_versions()]
    if unknown:
        parser.error(f"unknown version(s): {', '.join(unknown)}")
    cases = load_dataset()
    if args.cases:
        wanted = args.cases.split(",")
        cases = [c for c in cases if c["id"] in wanted]
        missing = set(wanted) - {c["id"] for c in cases}
        if missing:
            parser.error(f"unknown case(s): {', '.join(sorted(missing))}")

    calls = planned_calls(cases, args.versions, args.repeat)
    print(
        f"Plan: {len(cases)} cases x {len(args.versions)} versions x {args.repeat} repeat(s) "
        f"= up to {calls} real LLM calls."
    )
    if not args.yes:
        print("Nothing was called. Add --yes to run.")
        return 2

    exporter = exporter or install_exporter()
    scores, records = run_eval(
        cases, args.versions, args.repeat, exporter, llm, progress=lambda m: print(m, flush=True)
    )
    report = render_report(scores, args.versions)
    print("\n" + report)

    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = results_dir / f"{stamp}.json"
    payload = {
        "created": stamp,
        "versions": args.versions,
        "repeat": args.repeat,
        "summary": summarise(scores),
        "runs": records,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
