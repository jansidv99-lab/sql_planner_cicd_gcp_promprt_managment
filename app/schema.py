"""The fixed sales schema: the single source of truth for allowed tables and columns."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


class SchemaError(ValueError):
    """Raised when a column selection is not valid for the schema."""


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str  # BigQuery type name
    description: str


TABLE_NAME = "sales"

COLUMNS: tuple[Column, ...] = (
    Column("order_date", "DATE", "Date the order was placed"),
    Column("quantity", "INT64", "Number of units ordered"),
    Column("product", "STRING", "Product name"),
    Column("amount", "NUMERIC", "Order value (currency)"),
)

COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in COLUMNS)

_BY_NAME = {c.name: c for c in COLUMNS}


def validate_selection(columns: Iterable[str]) -> tuple[str, ...]:
    """Return the selected columns de-duplicated and in schema order.

    Raises SchemaError if the selection is empty or contains unknown columns.
    Names are case-sensitive.
    """
    selected = set(columns)
    if not selected:
        raise SchemaError("Select at least one column")

    unknown = sorted(selected - _BY_NAME.keys())
    if unknown:
        raise SchemaError(f"Unknown column(s): {', '.join(unknown)}")

    return tuple(name for name in COLUMN_NAMES if name in selected)


def column_types(columns: Iterable[str]) -> list[str]:
    """Sorted, de-duplicated BigQuery types of a valid column selection (used by prompt hints)."""
    return sorted({_BY_NAME[name].sql_type for name in validate_selection(columns)})


def schema_for_prompt(columns: Iterable[str]) -> str:
    """Describe the table with only the selected columns, for the Planner and Coder prompts.

    This module owns the data (and the validation); the layout is the Jinja2 partial
    `app/prompt_templates/_partials/schema.j2` (Phase 15). Only selected columns are passed in,
    so no other column can ever appear in a prompt.
    """
    from app.prompts import render_schema  # local import: prompts is the presentation layer

    selected = [_BY_NAME[name] for name in validate_selection(columns)]
    return render_schema(TABLE_NAME, selected)
