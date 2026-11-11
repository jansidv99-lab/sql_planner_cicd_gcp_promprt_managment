"""Deterministic scoring for the prompt eval (Phase 18). Pure functions, no LLM, no I/O.

A case says what a good answer looks like as *properties* of the SQL (many SQL texts are
correct), checked on the sqlglot AST in the app's dialect:

  group_by:<col>       GROUP BY uses the column (directly, by alias, or by position)
  agg:<FUNC>(<col>)    an aggregate FUNC over the column, e.g. agg:SUM(amount); agg:COUNT(*)
  func:<NAME>          any function NAME appears, e.g. func:DATE_TRUNC, func:EXTRACT, func:ROUND
  filter:<col>         WHERE uses the column
  order:asc|desc       direction of the first ORDER BY key
  limit:<n>            LIMIT n

Alternatives: `a|b` passes if any alternative passes, for answers that are equally correct,
e.g. `agg:COUNT(*)|agg:COUNT(order_date)` or `func:DATE_TRUNC|func:EXTRACT`.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.chain import ChainResult, Status
from app.reviewer import DIALECT

CHECK_KINDS = ("group_by", "agg", "func", "filter", "order", "limit")
_AGG = re.compile(r"^(?P<func>[A-Z_]+)\((?P<col>\*|[a-z_]+)\)$")


# --- checks on the SQL ---------------------------------------------------------------------


def _parse(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read=DIALECT)
    except SqlglotError:
        return None


def _columns(node: exp.Expression) -> set[str]:
    return {c.name.lower() for c in node.find_all(exp.Column)}


def _group_columns(tree: exp.Expression) -> set[str]:
    """Columns used by GROUP BY, following aliases and positions back to the SELECT list."""
    group = tree.args.get("group")
    if not group:
        return set()
    selects = list(tree.selects)
    aliases = {s.alias.lower(): _columns(s) for s in selects if isinstance(s, exp.Alias)}
    used: set[str] = set()
    for item in group.expressions:
        if isinstance(item, exp.Literal) and item.is_int:  # GROUP BY 1
            index = int(item.this) - 1
            if 0 <= index < len(selects):
                used |= _columns(selects[index])
            continue
        for name in _columns(item):
            used |= aliases.get(name, {name})
    return used


def check(sql: str, spec: str) -> bool:
    """Evaluate one check spec (see the module docstring) against the SQL."""
    if "|" in spec:
        # Evaluate ALL alternatives (no short-circuit), so a mistyped one still raises.
        results = [check(sql, alternative) for alternative in spec.split("|")]
        return any(results)
    kind, _, arg = spec.partition(":")
    if kind not in CHECK_KINDS:
        raise ValueError(f"Unknown check {spec!r}")
    tree = _parse(sql)
    if tree is None:
        return False

    if kind == "group_by":
        return arg.lower() in _group_columns(tree)
    if kind == "agg":
        m = _AGG.match(arg)
        if not m:
            raise ValueError(f"Bad agg check {spec!r}")
        for node in tree.find_all(exp.AggFunc):
            if node.sql_name() != m["func"]:
                continue
            if m["col"] == "*" and node.find(exp.Star) is not None:
                return True
            if m["col"] in _columns(node):
                return True
        return False
    if kind == "func":
        return any(node.sql_name() == arg.upper() for node in tree.find_all(exp.Func))
    if kind == "filter":
        where = tree.args.get("where")
        return bool(where) and arg.lower() in _columns(where)
    if kind == "order":
        order = tree.args.get("order")
        if not order or not order.expressions:
            return False
        desc = bool(order.expressions[0].args.get("desc"))
        return desc == (arg.lower() == "desc")
    # limit
    limit = tree.args.get("limit")
    return bool(limit) and limit.expression.sql() == arg


# --- one run -------------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    version: str
    status: str
    status_ok: bool  # status is one of the expected ones
    review_passed: bool | None  # None when no SQL was produced
    checks: dict[str, bool] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0

    @property
    def checks_passed(self) -> int:
        return sum(self.checks.values())


def expected_statuses(case: dict) -> list[str]:
    expected = case["expect"]["status"]
    return [expected] if isinstance(expected, str) else list(expected)


def score_case(
    case: dict,
    version: str,
    result: ChainResult,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
) -> CaseScore:
    status_ok = result.status.value in expected_statuses(case)
    checks: dict[str, bool] = {}
    if result.status is Status.OK:  # property checks only make sense on accepted SQL
        checks = {spec: check(result.sql, spec) for spec in case["expect"].get("checks", [])}
    elif case["expect"].get("checks"):
        checks = {spec: False for spec in case["expect"]["checks"]}
    return CaseScore(
        case_id=case["id"],
        version=version,
        status=result.status.value,
        status_ok=status_ok,
        review_passed=result.review.passed if result.review else None,
        checks=checks,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
    )


# --- per-version summary ------------------------------------------------------------------


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _percentile(values: Sequence[int], q: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1])


def summarise(scores: Iterable[CaseScore]) -> dict[str, dict]:
    """Metrics per version: accuracy and pass rates in %, mean tokens, latency percentiles."""
    by_version: dict[str, list[CaseScore]] = {}
    for s in scores:
        by_version.setdefault(s.version, []).append(s)

    summary = {}
    for version, runs in by_version.items():
        reviewed = [r for r in runs if r.review_passed is not None]
        total_checks = sum(len(r.checks) for r in runs)
        latencies = sorted(r.latency_ms for r in runs)
        summary[version] = {
            "runs": len(runs),
            "status_accuracy": _pct(sum(r.status_ok for r in runs), len(runs)),
            "review_pass_rate": _pct(sum(bool(r.review_passed) for r in reviewed), len(reviewed)),
            "check_pass_rate": _pct(sum(r.checks_passed for r in runs), total_checks),
            "mean_prompt_tokens": round(statistics.mean(r.prompt_tokens for r in runs)),
            "mean_completion_tokens": round(statistics.mean(r.completion_tokens for r in runs)),
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
            "llm_errors": sum(r.status == Status.LLM_ERROR.value for r in runs),
        }
    return summary
