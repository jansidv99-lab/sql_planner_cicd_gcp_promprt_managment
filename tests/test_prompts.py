"""Phase 14: Jinja2 prompt templates.

The golden files in tests/golden/v1/ were captured from the original f-string prompts before the
move to templates. The rendered prompts must match them byte for byte.
"""

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from app import prompts
from app.schema import COLUMN_NAMES, schema_for_prompt

GOLDEN = Path(__file__).parent / "golden" / "v1"

SCHEMA = schema_for_prompt(COLUMN_NAMES)
REQUEST = "Total amount per product, highest first"
OUTLINE = (
    "1. Columns: product\n2. Filters: none\n3. Aggregations: SUM(amount) AS total_amount\n"
    "4. Grouping: product\n5. Ordering: total_amount DESC\n6. Limit: none"
)

RENDERS = {
    "planner_system": lambda: prompts.planner_system(),
    "planner_user": lambda: prompts.planner_user_prompt(SCHEMA, REQUEST),
    "coder_system": lambda: prompts.coder_system(),
    "coder_user": lambda: prompts.coder_user_prompt(SCHEMA, OUTLINE),
}


@pytest.mark.parametrize("name", sorted(RENDERS))
def test_render_matches_golden_byte_for_byte(name):
    expected = (GOLDEN / f"{name}.txt").read_bytes().decode("utf-8")
    assert RENDERS[name]() == expected


def test_every_template_parses_and_has_a_golden_file():
    names = prompts.check_templates()
    assert sorted(names) == sorted(f"v1/{n}.j2" for n in RENDERS)


def test_missing_variable_raises_instead_of_rendering_empty():
    with pytest.raises(UndefinedError):
        prompts.render("planner_user", schema_block=SCHEMA)  # no request


@pytest.mark.parametrize(
    ("render", "user_text"),
    [
        (lambda t: prompts.planner_user_prompt(SCHEMA, t), "{{ 7*7 }} {% if true %}x{% endif %}"),
        (lambda t: prompts.coder_user_prompt(SCHEMA, t), "{{ 7*7 }} {# hidden #}"),
    ],
)
def test_user_text_is_never_rendered_as_a_template(render, user_text):
    out = render(user_text)
    assert user_text in out
    assert "49" not in out


def test_tags_are_not_html_escaped():
    out = prompts.planner_user_prompt(SCHEMA, "a < b & c")
    assert "<request>\na < b & c\n</request>" in out
    assert "&lt;" not in out and "&amp;" not in out


def test_template_comments_never_reach_the_prompt():
    for render in RENDERS.values():
        assert "{#" not in render() and "Variables:" not in render()


def test_cannot_answer_prefix_comes_from_python_constant():
    assert f"   {prompts.CANNOT_ANSWER_PREFIX} <short reason" in prompts.planner_system()
